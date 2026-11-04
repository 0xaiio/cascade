"""运行环境自检：健康检查、依赖与 JS 运行时状态、代理连通性探测。

共同点：它们回答的都是「**这个环境能不能干活**」，而不是「某条业务数据怎么样」。
自检只读不写：不改设置、不改任务、不碰 cookies 文件。
"""

import asyncio
import logging
from collections.abc import Callable

from fastapi import APIRouter, Body
from sqlmodel import Session, select

from ..api_context import ContextDep, SessionDep
from ...host.connectivity import test_proxy
from ...infra.logging_setup import log_path
from ...host.proxy import resolve_proxy
from ...host.runtime_env import describe_environment_risks
from ...bilibili.bili_manager import BiliManager
from ...contracts.config import AppSettings
from ...contracts.models import Setting
from ...contracts.schemas import DiagnosticsRead, ProxyTestRead, ProxyTestRequest, SettingEffectiveRead
from ...download.ytdlp_service import YtDlpService

logger = logging.getLogger(__name__)

router = APIRouter()


def _as_text(value: object) -> str:
    """把配置值折成可比对、可显示的字符串。`None` 与空串视为同一个值（都是「没设置」）。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        # 与 `api_support.set_setting` 写库的形态一致：布尔存成 "1"/"0"。
        return "1" if value else "0"
    if isinstance(value, (list, tuple)):
        return ",".join(str(item) for item in value)
    return str(value)


def effective_settings(
    session: Session,
    settings: AppSettings,
    service: YtDlpService,
    bili: BiliManager,
) -> list[SettingEffectiveRead]:
    """把库里的配置与**这个进程实际在用的值**逐项对照。

    运行值一律从**真正持有它的对象**上取，而不是从 `AppSettings` 再读一遍：
    - 下载相关的几项在 `YtDlpService` 上（`apply_stored_settings` 会推给它）；
    - 自动重试三个旋钮在调度器的策略上（`032` 之后它是每轮现取的）。

    只读：**不调用 `apply_stored_settings`** —— 自检不该顺手改运行态，
    否则它就再也测不出「库改了、进程没跟上」这件事（那正是它存在的理由）。
    """
    stored = {row.key: row.value for row in session.exec(select(Setting)).all()}
    policy = bili.auto_retry.policy

    readers: dict[str, Callable[[], object]] = {
        "download_dir": lambda: getattr(service, "download_dir", settings.download_dir),
        "default_concurrency": lambda: settings.default_concurrency,
        "default_resolution": lambda: settings.default_resolution,
        "default_speed_limit_kbps": lambda: settings.default_speed_limit_kbps or "",
        "default_retries": lambda: settings.default_retries,
        "default_subtitle_languages": lambda: getattr(
            service, "default_subtitle_languages", settings.default_subtitle_languages
        ),
        "aria2c_connections": lambda: getattr(service, "aria2c_connections", settings.aria2c_connections),
        "proxy": lambda: getattr(service, "proxy", settings.proxy) or "",
        "completion_notification": lambda: settings.completion_notification,
        "bili_auto_retry_enabled": lambda: policy.enabled,
        "bili_auto_retry_interval_seconds": lambda: policy.interval_seconds,
        "bili_auto_retry_max_attempts": lambda: policy.max_attempts,
    }

    items: list[SettingEffectiveRead] = []
    for key, read in readers.items():
        runtime = _as_text(read())
        raw = stored.get(key)
        items.append(
            SettingEffectiveRead(
                key=key,
                stored=raw,
                runtime=runtime,
                # 库里从没写过这一项时没有可比的东西，不算差异（否则默认值全会被报成「不一致」）。
                differs=raw is not None and _as_text(raw) != runtime,
            )
        )
    return items


@router.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@router.get("/api/diagnostics", response_model=DiagnosticsRead)
def diagnostics(ctx: ContextDep, session: SessionDep) -> DiagnosticsRead:
    """依赖状态 + cookies 是否就绪 + 日志路径 + 被摘除的宿主环境变量 + 配置的「库里 vs 实际」。"""
    service = ctx.service
    dependencies = (
        service.get_dependency_status()
        if hasattr(service, "get_dependency_status")
        else {**service.get_ffmpeg_status(), "yt_dlp_version": None, "js_runtime": False}
    )
    dependencies = {
        **dependencies,
        "youtube_max_parallel_downloads": ctx.settings.youtube_max_parallel_downloads,
        "anti403_http_chunk_size_mb": ctx.settings.anti403_http_chunk_size_mb,
        "throttled_rate_kbps": ctx.settings.throttled_rate_kbps,
    }
    return DiagnosticsRead(
        cookies_enabled=ctx.settings.cookies_path.exists(),
        dependencies=dependencies,
        log_file=str(log_path()) if log_path() else None,
        sanitized_environment=describe_environment_risks(),
        settings_effective=effective_settings(session, ctx.settings, ctx.service, ctx.bili),
    )


@router.post("/api/diagnostics/runtime", response_model=DiagnosticsRead)
async def refresh_runtime_diagnostics(ctx: ContextDep, session: SessionDep) -> DiagnosticsRead:
    """重新做一次 JS 运行时/依赖探测。

    探测结果在服务里是记忆化的（每次构建 ydl_opts 都起子进程太贵），所以用户装完
    Node/Deno、或修好环境变量之后，需要一个明确的「重新自检」入口来打破缓存。
    """
    refresh = getattr(ctx.service, "refresh_js_runtime", None)
    if callable(refresh):
        await asyncio.to_thread(refresh)
    return diagnostics(ctx, session)


@router.post("/api/proxy/test", response_model=ProxyTestRead)
async def test_proxy_connection(ctx: ContextDep, request: ProxyTestRequest | None = Body(default=None)) -> ProxyTestRead:
    """用**当前生效**（或请求里临时指定）的代理真实访问一次 YouTube。

    不接受 ``proxy=""`` 之外的模糊语义：空 = 用当前设置，``direct`` = 强制直连，
    其余按代理地址处理 —— 与 ``PUT /api/settings`` 的语义完全一致，避免出现
    「测试用 A、下载用 B」这种最气人的偏差。
    """
    override = request.proxy if request else None
    # getattr 兜底：测试里的 fake service 没有 proxy 属性，而「读不到配置」的语义
    # 就是「没设置代理」，不该因此 500。
    configured = getattr(ctx.service, "proxy", None)
    resolution = resolve_proxy(override if override is not None else configured)
    result = await asyncio.to_thread(test_proxy, resolution)
    logger.info(
        "proxy test: ok=%s source=%s proxy=%s status=%s elapsed_ms=%s error=%s",
        result.ok,
        result.source,
        result.proxy or "<direct>",
        result.http_status,
        result.elapsed_ms,
        result.error or "-",
    )
    return ProxyTestRead(**result.to_detail())
