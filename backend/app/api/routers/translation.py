"""字幕翻译的 HTTP 入口。步骤 2 只有「本机模型连通性」一个接口；
开始/详情/编辑/重试/取消/导出在步骤 3 加入。

路由层只做取参、调领域服务、定状态码 —— 翻译规则一律在 `translation/` 里。
"""

from __future__ import annotations

from fastapi import APIRouter

from ..api_context import ContextDep
from ...contracts.schemas import TranslationProbeRead
from ...translation.local import LocalTranslationProvider, probe_local_provider
from ...translation.provider import TranslationError

router = APIRouter()


@router.post("/api/translation/probe", response_model=TranslationProbeRead)
def probe_translation(ctx: ContextDep) -> TranslationProbeRead:
    """用一条最短请求实测本机模型是否可用。

    **不抛 4xx**：「不可用」是一个正常结论，前端据此显示「还没配置好」而不是错误弹窗。
    """
    settings = ctx.settings
    provider = LocalTranslationProvider(
        base_url=settings.translation_base_url,
        model=settings.translation_model,
        timeout=settings.translation_timeout_seconds,
    )
    try:
        model = probe_local_provider(provider)
    except TranslationError as exc:
        # 失败文案不依赖 provider 一定有 `model` 属性（替身/未来其它实现不一定有）。
        return TranslationProbeRead(ok=False, message=str(exc), model=getattr(provider, "model", ""))
    return TranslationProbeRead(ok=True, message="本机模型可用", model=model)
