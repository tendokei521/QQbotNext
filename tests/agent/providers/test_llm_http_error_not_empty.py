"""适配器不得把 HTTP 错误吞成"空回复"（LLM 重试复盘回归）。

旧缺陷：``_request`` 捕获 ``_FatalError``（HTTP 400 余额不足/404 模型不存在）后
``return None``，``chat()`` 把它变成 ``LLMResponse(text="")``；回退层只看到"零产出"，
于是 ``classify_response`` 判成 ``transient 空回复（无计费）`` 并原样重发一次
（日志里表现为：ERROR 400 → WARNING 重试 [1/1] → 再一条 ERROR 400）。

修复后：适配器把异常带状态码抛出，回退层用 ``classify_exception`` 正确判确定性/瞬态。
"""

from __future__ import annotations

from app.agent.providers import PROVIDERS, chat_with_fallback
from app.agent.providers.anthropic import AnthropicProvider
from app.agent.providers.base import ProviderRequestError
from app.agent.providers.gemini import GeminiProvider
from app.agent.providers.openai_compat import (
    OpenAICompatProvider,
    _FatalError,
    _RetryableError,
)
from app.agent.providers.policy import FailureKind, classify_exception

CALLS: dict[str, int] = {}


class _HttpBad(OpenAICompatProvider):
    """真实适配器形状：_post 抛 400（确定性）。"""

    async def _post(self, api_key, payload, timeout):
        CALLS["bad"] = CALLS.get("bad", 0) + 1
        raise _FatalError(
            'HTTP 400: {"error":{"message":"You have insufficient credits"}}',
            code="HTTP 400",
            status_code=400,
        )


class _HttpRateLimited(OpenAICompatProvider):
    """每次 _post 都 429（瞬态，适配器内部会先重试 retry_attempts 次）。"""

    async def _post(self, api_key, payload, timeout):
        CALLS["limited"] = CALLS.get("limited", 0) + 1
        raise _RetryableError(
            "HTTP 429: slow down", code="HTTP 429", retry_after=0.01, status_code=429
        )


class _HttpOk(OpenAICompatProvider):
    async def _post(self, api_key, payload, timeout):
        CALLS["ok"] = CALLS.get("ok", 0) + 1
        return {"choices": [{"message": {"content": "备用模型的回答"}}]}


def _register(monkeypatch, name, cls):
    monkeypatch.setitem(PROVIDERS, name, cls)
    return {"provider": name, "model": "m", "api_key": "k", "max_tokens": 64}


# ==================== 非流式：400 必须抛、且不重试 ====================


async def test_fatal_http_error_raises_instead_of_empty(monkeypatch):
    CALLS.clear()
    cfg = _register(monkeypatch, "http_bad", _HttpBad)
    provider = _HttpBad(cfg)

    try:
        await provider.chat([{"role": "user", "content": "hi"}])
    except _FatalError as error:
        assert error.status_code == 400
    else:  # pragma: no cover - 回归时给出明确失败信息
        raise AssertionError("HTTP 400 必须抛出，不能返回空回复")
    assert CALLS["bad"] == 1


async def test_deterministic_http_error_stops_fallback_chain(monkeypatch):
    """400 余额不足：不换 provider、不重试，直接停链（与 policy 口径一致）。"""
    CALLS.clear()
    bad = _register(monkeypatch, "http_bad2", _HttpBad)
    good = _register(monkeypatch, "http_ok", _HttpOk)

    resp = await chat_with_fallback(
        [bad, good], [{"role": "user", "content": "hi"}], max_empty_retries=1, empty_retry_backoff=0
    )

    assert CALLS.get("bad") == 1, "确定性错误不该在本 provider 上重试"
    assert CALLS.get("ok", 0) == 0, "确定性错误不该继续尝试下一个 provider"
    assert resp.text == ""


async def test_transient_http_error_retries_only_inside_adapter_then_switches(monkeypatch):
    """429：适配器内部按 retry_attempts 重试；冒到回退层后只换 provider，不再重发。"""
    CALLS.clear()
    limited = _register(monkeypatch, "http_429", _HttpRateLimited)
    good = _register(monkeypatch, "http_ok2", _HttpOk)

    resp = await chat_with_fallback(
        [limited, good], [{"role": "user", "content": "hi"}], max_empty_retries=2, empty_retry_backoff=0
    )

    assert CALLS.get("limited") == 2, "adapter 内部 retry_attempts=2"
    assert CALLS.get("ok") == 1
    assert resp.text == "备用模型的回答"


async def test_empty_200_still_goes_through_empty_reply_retry(monkeypatch):
    """真正的「200 但零产出」仍走空回复重试——别把合法场景一起误伤。"""

    class _Empty200(OpenAICompatProvider):
        async def _post(self, api_key, payload, timeout):
            CALLS["empty"] = CALLS.get("empty", 0) + 1
            return {"choices": []}

    CALLS.clear()
    cfg = _register(monkeypatch, "http_empty", _Empty200)

    resp = await chat_with_fallback(
        [cfg], [{"role": "user", "content": "hi"}], max_empty_retries=1, empty_retry_backoff=0
    )

    assert CALLS.get("empty") == 2, "空响应仍应重试一次"
    assert resp.text == ""


# ==================== 分类器：状态码优先 ====================


def test_classifier_reads_status_code_from_exception():
    failure = classify_exception(
        _FatalError("HTTP 400: insufficient credits", code="HTTP 400", status_code=400)
    )
    assert failure.kind == FailureKind.DETERMINISTIC

    limited = classify_exception(
        _RetryableError("HTTP 429: slow down", code="HTTP 429", status_code=429)
    )
    assert limited.kind == FailureKind.TRANSIENT


def test_classifier_reads_status_from_text_for_third_party_errors():
    """第三方适配器只抛带文案的异常：也要从 "HTTP 404" 里认出确定性。"""
    failure = classify_exception(RuntimeError("HTTP 404: model not found"))
    assert failure.kind == FailureKind.DETERMINISTIC


# ==================== Gemini / Anthropic 非流式同样抛 ====================


class _FakeResponse:
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self._body = body

    async def text(self) -> str:
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _FakeSession:
    def __init__(self, status: int, body: str) -> None:
        self._response = _FakeResponse(status, body)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def post(self, *args, **kwargs):
        return self._response


def _patch_session(monkeypatch, module, status: int, body: str) -> None:
    monkeypatch.setattr(
        module.aiohttp, "ClientSession", lambda *a, **k: _FakeSession(status, body)
    )


async def test_gemini_non_200_raises_with_status(monkeypatch):
    from app.agent.providers import gemini as gemini_module

    _patch_session(monkeypatch, gemini_module, 400, '{"error":"insufficient credits"}')
    provider = GeminiProvider({"api_key": "K", "model": "gemini-2.0-flash"})

    try:
        await provider.chat([{"role": "user", "content": "hi"}])
    except ProviderRequestError as error:
        assert error.status_code == 400
    else:  # pragma: no cover
        raise AssertionError("Gemini 非 200 必须抛出，不能返回空回复")


async def test_anthropic_non_200_raises_with_status(monkeypatch):
    from app.agent.providers import anthropic as anthropic_module

    _patch_session(monkeypatch, anthropic_module, 400, '{"error":"insufficient credits"}')
    provider = AnthropicProvider({"api_key": "K", "model": "claude-3-5-sonnet-20241022"})

    try:
        await provider.chat([{"role": "user", "content": "hi"}])
    except ProviderRequestError as error:
        assert error.status_code == 400
    else:  # pragma: no cover
        raise AssertionError("Anthropic 非 200 必须抛出，不能返回空回复")


def test_empty_reply_retry_contract_is_still_declared():
    """空响应的分类口径没被改坏：无产出、无计费 → transient 空回复。"""
    from app.agent.providers.policy import classify_response

    failure = classify_response(text="", usage={})
    assert failure is not None
    assert failure.kind == FailureKind.TRANSIENT
    assert "空回复" in failure.detail
