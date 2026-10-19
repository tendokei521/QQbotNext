"""Provider 适配层的「静默失败」回归：URL 拼装、思考片段归属。

这两类缺陷用户侧都只表现为「抱歉，我暂时无法回答」，日志里看不出真因：

1. **Gemini 非流式 URL 畸形**：``action`` 里的 ``?alt=sse`` 顺带让流式的 ``&key=`` 合法，
   非流式却变成 ``...:models/gemini-x:generateContent&key=KEY``——``key`` 落在 path 里、
   从不上到 query，代码也没发 ``x-goog-api-key`` 头。默认 ``stream_output=False``，
   于是默认配置的 Gemini 每一轮都 401/404，被降级成空回复。
2. **Gemini 非流式把 thought 片段当正文**：流式分支早已 ``kind = "reasoning" if p.get("thought")``，
   非流式此前把所有 part 的 text 一起拼进正文 —— 用户的回复里直接出现模型内心独白；
   思考吃满 ``maxOutputTokens`` 时更糟：``text`` 非空 ⇒ 上层不判空、不触发收敛轮，
   用户收到的「整条回复」全是思考碎片。
3. **Anthropic 非流式丢弃 thinking 块**、**OpenAI 兼容非流式只认 reasoning_content**：
   导致上层「思考吃满预算」的收敛轮与诊断对它们全部失效。
"""

from __future__ import annotations

import pytest

from app.llm.providers.gemini import GeminiProvider

# ==================== 1. Gemini URL 拼装 ====================


def test_gemini_non_stream_url_has_query_key():
    """非流式必须自带 ``?key=``（此前整段落在 path 里，key 从不上到 query）。"""
    url = GeminiProvider({"api_key": "KEY123"})._url("gemini-2.0-flash", stream=False)

    assert url.endswith(":generateContent?key=KEY123")
    assert "&key=" not in url


def test_gemini_stream_url_keeps_alt_sse():
    url = GeminiProvider({"api_key": "KEY123"})._url("gemini-2.0-flash", stream=True)

    assert url.endswith(":streamGenerateContent?alt=sse&key=KEY123")


@pytest.mark.parametrize(
    "api_base",
    [
        "https://generativelanguage.googleapis.com/v1beta",
        "https://generativelanguage.googleapis.com",
        "https://my-proxy.example.com/v1beta/",
    ],
)
def test_gemini_base_is_normalized_once(api_base):
    """api_base 带不带 /v1beta 都必须只出现一次（此前 get_models 会拼成 /v1beta/v1beta）。"""
    provider = GeminiProvider({"api_key": "K", "api_base": api_base})
    assert provider._base().count("/v1beta") == 1
    assert provider._url("m").count("/v1beta") == 1


async def test_gemini_get_models_url_is_not_doubled(monkeypatch):
    seen: dict = {}
    _fake_aiohttp(monkeypatch, {"models": [{"name": "models/gemini-2.0-flash"}]}, seen)

    models = await GeminiProvider({"api_key": "K"}).get_models()

    assert models == ["gemini-2.0-flash"]
    assert seen["url"].count("/v1beta") == 1
    assert "?key=K" in seen["url"]


# ==================== 2. 思考片段归属（非流式） ====================

THOUGHT_PARTS = [
    {"text": "让我想想这张图……", "thought": True},
    {"text": "图里是一只猫。"},
]


def _fake_aiohttp(monkeypatch, payload, seen: dict | None = None):
    """把 aiohttp.ClientSession 换成返回固定 JSON 的桩，并记录请求 URL。"""
    import aiohttp

    class _Resp:
        status = 200

        async def json(self):
            return payload

        async def text(self):
            return ""

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Session:
        def post(self, url, **kw):
            if seen is not None:
                seen["url"] = url
            return _Resp()

        def get(self, url, **kw):
            if seen is not None:
                seen["url"] = url
            return _Resp()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: _Session())


async def test_gemini_non_stream_thought_is_reasoning_not_text(monkeypatch):
    _fake_aiohttp(monkeypatch, {
        "candidates": [{"content": {"parts": THOUGHT_PARTS}}],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
    })
    provider = GeminiProvider({"api_key": "K"})

    resp = await provider.chat([{"role": "user", "content": "看图"}], model="gemini-2.0-flash")

    assert resp.text == "图里是一只猫。", "思考不能出现在用户可见正文里"
    assert resp.reasoning == "让我想想这张图……"


async def test_gemini_thought_only_response_is_not_ok(monkeypatch):
    """只有思考 = 正文为空（上层据此触发收敛轮/告警，而不是把独白发出去）。"""
    _fake_aiohttp(monkeypatch, {
        "candidates": [{"content": {"parts": [{"text": "想了很久", "thought": True}]}}],
    })
    provider = GeminiProvider({"api_key": "K"})

    resp = await provider.chat([{"role": "user", "content": "看图"}], model="gemini-2.0-flash")

    assert resp.text == ""
    assert not resp.ok
    assert resp.reasoning == "想了很久"


@pytest.mark.parametrize(
    ("api_base", "expected_url"),
    [
        ("https://api.anthropic.com", "https://api.anthropic.com/v1/messages"),
    ],
)
async def test_anthropic_non_stream_thinking_block_is_reasoning(monkeypatch, api_base, expected_url):
    seen: dict = {}
    _fake_aiohttp(monkeypatch, {
        "content": [
            {"type": "thinking", "thinking": "先分析一下"},
            {"type": "text", "text": "答案是 42。"},
        ],
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }, seen)
    from app.llm.providers.anthropic import AnthropicProvider

    provider = AnthropicProvider({"api_key": "K", "api_base": api_base})

    resp = await provider.chat([{"role": "user", "content": "hi"}], model="claude-x")

    assert resp.text == "答案是 42。"
    assert resp.reasoning == "先分析一下"


def test_openai_compat_non_stream_accepts_reasoning_alias():
    """中转商把思考写在 message.reasoning（流式早已两种都认，非流式此前只认一个）。"""
    from app.llm.providers.openai_compat import OpenAICompatProvider

    provider = OpenAICompatProvider({"api_key": "K"})
    resp = provider._to_response({"choices": [{"message": {"content": "", "reasoning": "想过"}}]})

    assert resp.text == ""
    assert resp.reasoning == "想过"


def test_openai_compat_non_stream_still_accepts_reasoning_content():
    from app.llm.providers.openai_compat import OpenAICompatProvider

    provider = OpenAICompatProvider({"api_key": "K"})
    resp = provider._to_response(
        {"choices": [{"message": {"content": "答", "reasoning_content": "想过"}}]}
    )

    assert (resp.text, resp.reasoning) == ("答", "想过")
