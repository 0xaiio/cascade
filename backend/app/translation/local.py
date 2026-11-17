"""只走**本机**模型的翻译后端：OpenAI 兼容的 `/v1/chat/completions`。

当前阶段刻意只支持本地模型（`llama-server` 等服务暴露的兼容接口）：不处理 API Key、
不做任何外部网络调用。运行时因此**不需要新增任何依赖** —— HTTP 用标准库 `urllib.request`。

为什么选 OpenAI 兼容协议而不是某个库：本机推理服务（`llama-server` / Ollama / LM Studio）
普遍提供同一套 HTTP 形态；换模型只需要改地址和模型名，不需要改代码。

错误分层：`HttpJsonTransport` 只负责「发出去、拿回来」，把 HTTP/JSON 故障收敛成
`TranslationHttpError`；`LocalTranslationProvider` 再把它翻译成用户可读的
`TranslationError`。这样 401 / 429 / 5xx / 超时 / 坏 JSON 各有各的说法，
不会被笼统报成「翻译失败」。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from typing import Protocol

from ..contracts.config import (
    DEFAULT_TRANSLATION_BASE_URL as DEFAULT_BASE_URL,
    DEFAULT_TRANSLATION_MODEL as DEFAULT_MODEL,
    DEFAULT_TRANSLATION_TIMEOUT_SECONDS as DEFAULT_TIMEOUT_SECONDS,
)
from .provider import (
    TranslationError,
    TranslationItem,
    TranslationReply,
    replies_from_raw,
)

SYSTEM_PROMPT = """你是一个字幕翻译器。把用户给出的英文字幕逐条翻译成简体中文。

严格要求：
1. 只输出一个 JSON 数组，形如 [{"i":0,"text":"译文"},{"i":1,"text":"译文"}]。
2. 数组长度、顺序与输入完全一致；每个 i 必须是输入里给的整数，不多、不少、不重复。
3. 一条输入对应一条输出。禁止拆条、合条、补写、漏译；禁止输出时间码。
4. text 只放译文本身，不要解释、不要引号包裹的编号、不要 markdown 标记。
5. 专有名词、人名、数字、代码标识符原样保留；术语在全批内保持一致。
6. 输入里若附带 context，它只是上一句原文，供你理解语气，**不要翻译它、不要写进输出**。
"""


class TranslationHttpError(Exception):
    """本机模型服务的传输层故障。`status` 为 None 表示没拿到 HTTP 响应。"""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class HttpJsonTransport(Protocol):
    def post_json(
        self, url: str, payload: dict, *, timeout: float
    ) -> dict: ...


class UrllibJsonTransport:
    """标准库实现；不引第三方依赖。"""

    def post_json(self, url: str, payload: dict, *, timeout: float) -> dict:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
            raise TranslationHttpError(detail or exc.reason, status=exc.code) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TranslationHttpError(str(exc)) from exc
        try:
            data = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise TranslationHttpError("模型服务返回的不是 JSON") from exc
        if not isinstance(data, dict):
            raise TranslationHttpError("模型服务返回的不是 JSON 对象")
        return data


def validate_local_base_url(url: str) -> str:
    """只接受**本机**地址。当前阶段不允许把字幕发到外部服务。

    这是安全边界，不是偏好：用户在设置里填 `https://…` 时直接拒绝，
    而不是「先试试再说」。localhost / 127.0.0.1 / ::1 都算本机。
    """
    value = (url or "").strip()
    if not value:
        raise ValueError("本机模型地址不能为空")
    parts = urllib.parse.urlsplit(value)
    if parts.scheme not in {"http", "https"}:
        raise ValueError("本机模型地址必须用 http 或 https")
    if parts.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("当前阶段只支持本机模型，地址必须是 127.0.0.1 / localhost / ::1")
    return value.rstrip("/")


def _http_error_message(exc: TranslationHttpError) -> str:
    status = exc.status
    if status in (401, 403):
        return "本机模型服务拒绝了请求（401/403），检查服务配置"
    if status == 404:
        return "本机模型服务没有这个接口（404），检查地址是否以 /v1 结尾"
    if status == 429:
        return "本机模型服务请求太频繁（429），稍后再试"
    if status is not None and status >= 500:
        return f"本机模型服务出错了（HTTP {status}）"
    if status is not None:
        return f"本机模型服务返回 HTTP {status}：{exc}"
    return f"连不上本机模型服务：{exc}"


def _extract_reply_text(payload: dict) -> str:
    try:
        choices = payload["choices"]
        content = choices[0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise TranslationError("模型回复里没有内容") from exc
    if not isinstance(content, str) or not content.strip():
        raise TranslationError("模型回复的内容是空的")
    return content.strip()


def parse_model_json(content: str) -> list:
    """从模型文本里取出 JSON 数组；允许包一层 ```json 代码块。"""
    text = content.strip()
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.startswith("```")]
        text = "\n".join(lines).strip()
    try:
        parsed = json.loads(text)
    except ValueError:
        start, end = text.find("["), text.rfind("]")
        if start < 0 or end <= start:
            raise TranslationError("模型回复不是 JSON 数组") from None
        try:
            parsed = json.loads(text[start : end + 1])
        except ValueError:
            raise TranslationError("模型回复不是合法 JSON") from None
    if isinstance(parsed, dict) and isinstance(parsed.get("items"), list):
        parsed = parsed["items"]
    if not isinstance(parsed, list):
        raise TranslationError("模型回复不是 JSON 数组")
    return parsed


class LocalTranslationProvider:
    """把条目交给本机模型服务。实现 `TranslationProvider` 协议。"""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        transport: HttpJsonTransport | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.base_url = validate_local_base_url(base_url or DEFAULT_BASE_URL)
        self.model = model or DEFAULT_MODEL
        self.transport = transport or UrllibJsonTransport()
        self.timeout = timeout

    def _request(self, items: Sequence[TranslationItem], *, context: str = "") -> dict:
        payload_items = [{"i": item.index, "text": item.text} for item in items]
        user_content = json.dumps({"context": context, "items": payload_items}, ensure_ascii=False)
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0,
            # Qwen3 默认会先输出一大段 <think>。字幕翻译不需要推理：
            # 关掉它既快得多，也避免思考文字混进 JSON 回复。不支持该字段的服务会忽略它。
            "chat_template_kwargs": {"enable_thinking": False},
        }
        try:
            return self.transport.post_json(
                f"{self.base_url}/chat/completions", payload, timeout=self.timeout
            )
        except TranslationHttpError as exc:
            raise TranslationError(_http_error_message(exc)) from exc

    def translate(self, items: Sequence[TranslationItem], *, context: str = "") -> list[TranslationReply]:
        if not items:
            return []
        content = _extract_reply_text(self._request(items, context=context))
        return replies_from_raw(parse_model_json(content))


def probe_local_provider(provider: LocalTranslationProvider) -> str:
    """连通性检查：真的发一条最短请求，而不是只 GET 一个模型列表。

    返回模型名（服务没给名字时用 `local`）。失败抛 `TranslationError`，由路由翻成 400。
    """
    replies = provider.translate([TranslationItem(index=0, text="Hello")])
    if len(replies) != 1 or replies[0].index != 0 or not replies[0].text.strip():
        raise TranslationError("本机模型服务没有按约定返回一条译文")
    return provider.model
