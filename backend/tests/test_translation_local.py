"""本机模型 provider 与设置契约。全部离线：HTTP 边界是假 transport。

这一层最容易写错的不是「请求发得出去」，而是**故障被说成同一句话**：
401 / 429 / 5xx / 超时 / 坏 JSON 必须各自可辨，用户才知道是该改配置还是该等一会儿。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.api.routers.translation as translation_router
from app.contracts.config import DEFAULT_TRANSLATION_BASE_URL, DEFAULT_TRANSLATION_MODEL
from app.main import create_app
from app.translation.local import (
    LocalTranslationProvider,
    TranslationError,
    TranslationHttpError,
    parse_model_json,
    probe_local_provider,
    validate_local_base_url,
)
from app.translation.provider import TranslationItem
from test_api import make_client
from translation_fakes import FakeJsonTransport, _chat_reply


def provider(transport: FakeJsonTransport) -> LocalTranslationProvider:
    return LocalTranslationProvider(
        base_url="http://127.0.0.1:8080/v1", model="qwen3-8b", transport=transport, timeout=7
    )


ITEMS = [TranslationItem(index=0, text="Hello"), TranslationItem(index=1, text="World")]


# ---------------------------------------------------------------- 请求形态
def test_request_uses_local_openai_compatible_endpoint_and_payload() -> None:
    fake = FakeJsonTransport(responses=[_chat_reply('[{"i":0,"text":"你好"},{"i":1,"text":"世界"}]')])
    got = provider(fake).translate(ITEMS, context="上一句")

    assert got[0].text == "你好" and got[1].text == "世界"
    url, payload, timeout = fake.calls[0]
    assert url == "http://127.0.0.1:8080/v1/chat/completions"
    assert timeout == 7
    assert payload["model"] == "qwen3-8b"
    assert payload["temperature"] == 0
    # Qwen3 的思考模式会吐 <think> 段落，既慢又会污染 JSON —— 明确关掉。
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}
    user = json.loads(payload["messages"][1]["content"])
    assert user == {
        "context": "上一句",
        "items": [{"i": 0, "text": "Hello"}, {"i": 1, "text": "World"}],
    }
    assert "JSON" in payload["messages"][0]["content"]


def test_empty_batch_sends_nothing() -> None:
    fake = FakeJsonTransport()
    assert provider(fake).translate([]) == []
    assert fake.calls == []


# ---------------------------------------------------------------- 回复解析
@pytest.mark.parametrize(
    "content",
    [
        '[{"i":0,"text":"你好"}]',
        '```json\n[{"i":0,"text":"你好"}]\n```',
        '当然，译文如下：\n[{"i":0,"text":"你好"}]\n希望有帮助。',
        '{"items":[{"i":0,"text":"你好"}]}',
    ],
)
def test_model_json_tolerates_wrapping_but_keeps_the_array(content: str) -> None:
    assert parse_model_json(content) == [{"i": 0, "text": "你好"}]


def test_model_json_refuses_non_array_text() -> None:
    with pytest.raises(TranslationError, match="不是 JSON 数组"):
        parse_model_json("你好，这是译文")


def test_model_reply_without_choices_is_an_error() -> None:
    fake = FakeJsonTransport(responses=[{"choices": []}])
    with pytest.raises(TranslationError, match="没有内容"):
        provider(fake).translate(ITEMS)


def test_misaligned_reply_is_carried_as_data_then_rejected_by_align() -> None:
    """分层纪律：HTTP provider 只把回复变成类型化数据，**拒绝错位是 align 的职责**。"""
    fake = FakeJsonTransport(responses=[_chat_reply('[{"i":0,"text":"你好"},{"i":7,"text":"世界"}]')])
    replies = provider(fake).translate(ITEMS)
    assert [reply.index for reply in replies] == [0, 7]

    from app.translation.align import apply_chunk
    from app.translation.provider import TranslationChunk

    chunk = TranslationChunk(index=0, items=tuple(ITEMS))
    with pytest.raises(TranslationError, match="序号对不上"):
        apply_chunk(chunk, replies)


# ---------------------------------------------------------------- 故障分类
@pytest.mark.parametrize(
    ("status", "needle"),
    [
        (401, "401/403"),
        (403, "401/403"),
        (404, "404"),
        (429, "429"),
        (500, "HTTP 500"),
    ],
)
def test_http_statuses_get_distinct_readable_messages(status: int, needle: str) -> None:
    fake = FakeJsonTransport()
    fake.queue(error=TranslationHttpError("bad", status=status))
    with pytest.raises(TranslationError, match=needle):
        provider(fake).translate(ITEMS)


def test_connection_failure_is_not_reported_as_a_model_error() -> None:
    fake = FakeJsonTransport()
    fake.queue(error=TranslationHttpError("Connection refused"))
    with pytest.raises(TranslationError, match="本机模型服务没有运行"):
        provider(fake).translate(ITEMS)


def test_winerror_10061_is_translated_into_an_actionable_hint() -> None:
    """连接被拒是「服务没起」，原始 WinError 对用户没有任何可操作性。"""
    fake = FakeJsonTransport()
    fake.queue(error=TranslationHttpError("<urlopen error [WinError 10061] 由于目标计算机积极拒绝，无法连接。>"))
    with pytest.raises(TranslationError) as caught:
        provider(fake).translate(ITEMS)
    assert "10061" not in str(caught.value)
    assert "model-server.log" in str(caught.value)
    assert "自动拉起" in str(caught.value)


def test_non_json_response_is_reported_as_such() -> None:
    fake = FakeJsonTransport()
    fake.queue(error=TranslationHttpError("模型服务返回的不是 JSON"))
    with pytest.raises(TranslationError, match="不是 JSON"):
        provider(fake).translate(ITEMS)


# ---------------------------------------------------------------- 本地边界
@pytest.mark.parametrize("url", ["http://127.0.0.1:8080/v1", "http://localhost:11434/v1", "https://[::1]:8443/v1"])
def test_loopback_urls_are_allowed(url: str) -> None:
    assert validate_local_base_url(url).endswith("/v1") or url.endswith("/v1")


@pytest.mark.parametrize(
    "url",
    ["https://api.openai.com/v1", "http://example.com/v1", "ftp://127.0.0.1/v1", ""],
)
def test_external_or_empty_urls_are_refused(url: str) -> None:
    with pytest.raises(ValueError):
        validate_local_base_url(url)


def test_provider_itself_refuses_an_external_url() -> None:
    with pytest.raises(ValueError, match="只支持本机"):
        LocalTranslationProvider(base_url="https://api.example.com/v1")


def test_probe_really_sends_a_minimal_request() -> None:
    fake = FakeJsonTransport(responses=[_chat_reply('[{"i":0,"text":"你好"}]')])
    assert probe_local_provider(provider(fake)) == "qwen3-8b"
    assert len(fake.calls) == 1
    assert json.loads(fake.calls[0][1]["messages"][1]["content"])["items"][0]["text"] == "Hello"


def test_probe_reports_failure_without_throwing_at_the_router() -> None:
    fake = FakeJsonTransport()
    fake.queue(error=TranslationHttpError("Connection refused"))
    with pytest.raises(TranslationError, match="本机模型服务没有运行"):
        probe_local_provider(provider(fake))


# ---------------------------------------------------------------- 设置契约
def test_settings_defaults_point_to_a_local_model() -> None:
    assert DEFAULT_TRANSLATION_BASE_URL.startswith("http://127.0.0.1")
    assert DEFAULT_TRANSLATION_MODEL == "local"


def test_settings_roundtrip_and_external_url_rejected(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    current = client.get("/api/settings").json()
    assert current["translation_base_url"] == DEFAULT_TRANSLATION_BASE_URL
    assert current["translation_model"] == DEFAULT_TRANSLATION_MODEL

    saved = client.put(
        "/api/settings",
        json={"translation_base_url": "http://127.0.0.1:9999/v1", "translation_model": "qwen3-8b"},
    )
    assert saved.status_code == 200
    assert saved.json()["translation_model"] == "qwen3-8b"
    assert client.get("/api/settings").json()["translation_base_url"] == "http://127.0.0.1:9999/v1"

    refused = client.put("/api/settings", json={"translation_base_url": "https://api.example.com/v1"})
    assert refused.status_code == 400
    assert "只支持本机" in refused.json()["detail"]
    assert client.get("/api/settings").json()["translation_base_url"] == "http://127.0.0.1:9999/v1"


def test_probe_endpoint_turns_a_service_failure_into_a_normal_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Broken:
        model = "local"

        def __init__(self, **kwargs) -> None:
            pass

        def translate(self, items, *, context=""):
            raise TranslationError("连不上本机模型服务：Connection refused")

    monkeypatch.setattr(translation_router, "LocalTranslationProvider", Broken)
    client: TestClient = make_client(tmp_path)
    response = client.post("/api/translation/probe")
    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert "连不上" in response.json()["message"]


def test_probe_endpoint_reports_the_model_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Working:
        model = "local"

        def __init__(self, **kwargs) -> None:
            pass

        def translate(self, items, *, context=""):
            from app.translation.provider import TranslationReply

            return [TranslationReply(index=item.index, text="你好") for item in items]

    monkeypatch.setattr(translation_router, "LocalTranslationProvider", Working)
    client: TestClient = make_client(tmp_path)
    body = client.post("/api/translation/probe").json()
    assert body["ok"] is True
    assert body["model"] == "local"
