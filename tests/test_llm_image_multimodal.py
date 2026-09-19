"""多模态图片同传测试。

背景：图片此前在 ``MessageEvent.text`` 处就被丢弃，只有 ``[图片]`` 占位文本进模型；
`supports_image` / `sanitize_contexts_by_modalities` 只是"清洗侧"的半截工程，
没有任何地方构造过图片块。本文件锁住"生成侧"：

1. OneBot 图片段 → 可用图片载荷（http/data/base64/file 四种形态）；
2. 拿不到可用来源的图片（``file: xxx.image``）不硬塞，若 bot 能 get_image 则兜底；
3. 体积超限/数量超限跳过；
4. OpenAI 风格 content 块组装（纯图片时补引导文本）；
5. Anthropic / Gemini 各自的图片块翻译（此前会把 dict 压成纯文本 / str(dict)）；
6. `assembly.place_images` 的开关与模态门控（文本模型保持 [图片] 占位语义），
   以及 `assembly.sanitize` 作为最后一层兜底（漏进来的图块也会被替换）。
"""

from __future__ import annotations

import base64

import pytest

from app.domain.events import GroupMessageEvent, MessageSegment, UserInfo
from app.llm.assembly import PromptRequest, place_images
from app.llm.image import (
    build_user_content,
    collect_image_segments,
    count_images,
    image_url_from_bytes,
    max_image_bytes,
    resolve_images,
)
from app.llm.providers.anthropic import _normalize_messages as anthropic_normalize
from app.llm.providers.gemini import _normalize_messages as gemini_normalize

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 32
PNG_B64 = base64.b64encode(PNG_BYTES).decode()
PNG_DATA_URL = f"data:image/png;base64,{PNG_B64}"


def _event(segments):
    return GroupMessageEvent(
        event_type="message_group",
        message_type="group",
        time=1788342260,
        user_id=20002,
        self_id=3569937952,
        message=[MessageSegment(t, d) for t, d in segments],
        user=UserInfo(user_id=20002, nickname="小明"),
        group=type("G", (), {"group_id": 778})(),
    )


# ---------- 解析 ----------


async def test_resolve_http_url():
    images = await resolve_images(_event([("image", {"url": "https://cdn.example/a.png"})]))
    assert images == [{"kind": "url", "value": "https://cdn.example/a.png"}]


async def test_resolve_data_url_kept_as_is():
    images = await resolve_images(_event([("image", {"file": PNG_DATA_URL})]))
    assert images[0]["kind"] == "url"
    assert images[0]["value"].startswith("data:image/png;base64,")


async def test_resolve_base64_scheme_uses_named_mime():
    images = await resolve_images(
        _event([("image", {"base64": f"base64://{PNG_B64}", "file": "a.jpg"})])
    )
    assert images == [{"kind": "base64", "value": PNG_B64, "mime": "image/jpeg"}]


async def test_file_only_without_bot_is_skipped():
    """``file: abc.image`` 是 QQ 内部名，没有 URL 时不能硬塞给 API。"""
    images = await resolve_images(_event([("image", {"file": "abc.image"})]))
    assert images == []


async def test_file_only_with_bot_falls_back_to_get_image():
    class _Bot:
        def __init__(self):
            self.calls = []

        async def get_image(self, file):
            self.calls.append(file)
            return {"status": "ok", "data": {"url": "http://127.0.0.1:3000/img/a.png"}}

    bot = _Bot()
    images = await resolve_images(_event([("image", {"file": "abc.image"})]), bot=bot)
    assert bot.calls == ["abc.image"]
    assert images == [{"kind": "url", "value": "http://127.0.0.1:3000/img/a.png"}]


async def test_get_image_without_url_is_skipped():
    class _Bot:
        async def get_image(self, file):
            return {"status": "ok", "data": {}}

    images = await resolve_images(_event([("image", {"file": "abc.image"})]), bot=_Bot())
    assert images == []


async def test_get_image_exception_is_swallowed():
    class _Bot:
        async def get_image(self, file):
            raise RuntimeError("连接已断开")

    images = await resolve_images(_event([("image", {"file": "abc.image"})]), bot=_Bot())
    assert images == []


async def test_size_limit_skips_huge_image():
    big = base64.b64encode(b"x" * 4096).decode()
    images = await resolve_images(_event([("image", {"base64": f"base64://{big}"})]), max_bytes=1024)
    assert images == []


async def test_max_count_limits_images():
    event = _event([("image", {"url": f"https://cdn.example/{i}.png"}) for i in range(5)])
    images = await resolve_images(event, max_images=2)
    assert len(images) == 2


def test_count_and_collect_images():
    event = _event([("text", {"text": "看图"}), ("image", {"url": "http://a/1.png"}), ("image", {})])
    assert count_images(event) == 2
    assert len(collect_image_segments(event)) == 2


def test_max_image_bytes_from_config():
    assert max_image_bytes({}) == 10 * 1024 * 1024
    assert max_image_bytes({"image_max_mb": 5}) == 5 * 1024 * 1024
    assert max_image_bytes({"image_max_mb": "abc"}) == 10 * 1024 * 1024
    assert max_image_bytes({"image_max_mb": 0}) == 10 * 1024 * 1024


def test_image_url_from_bytes_sniffs_mime():
    url = image_url_from_bytes(PNG_BYTES)
    assert url is not None and url.startswith("data:image/png;base64,")
    assert image_url_from_bytes(b"") is None


# ---------- content 块组装 ----------


def test_build_user_content_keeps_text_and_image_order():
    content = build_user_content(
        [{"kind": "url", "value": "https://a/1.png"}], text="看看这个"
    )
    assert content == [
        {"type": "text", "text": "看看这个"},
        {"type": "image_url", "image_url": {"url": "https://a/1.png"}},
    ]


def test_build_user_content_image_only_adds_lead_text():
    content = build_user_content([{"kind": "url", "value": "https://a/1.png"}], text="")
    assert content[0]["type"] == "text"
    assert "图片" in content[0]["text"]


def test_build_user_content_without_images_returns_none():
    assert build_user_content([], text="只有文字") is None
    assert build_user_content([{"kind": "url", "value": ""}], text="x") is None


# ---------- provider 翻译 ----------


def test_anthropic_translates_image_blocks():
    _system, messages = anthropic_normalize([
        {"role": "user", "content": [
            {"type": "text", "text": "看这个"},
            {"type": "image_url", "image_url": {"url": "https://a/1.png"}},
            {"type": "image_url", "image_url": {"url": PNG_DATA_URL}},
        ]},
    ])
    blocks = messages[0]["content"]
    assert blocks[0] == {"type": "text", "text": "看这个"}
    assert blocks[1] == {"type": "image", "source": {"type": "url", "url": "https://a/1.png"}}
    assert blocks[2]["source"]["type"] == "base64"
    assert blocks[2]["source"]["media_type"] == "image/png"
    assert blocks[2]["source"]["data"] == PNG_B64


def test_anthropic_plain_string_content_still_works():
    _system, messages = anthropic_normalize([{"role": "user", "content": "你好"}])
    assert messages[0]["content"] == [{"type": "text", "text": "你好"}]


def test_gemini_translates_image_blocks():
    contents, _system = gemini_normalize([
        {"role": "user", "content": [
            {"type": "text", "text": "看这个"},
            {"type": "image_url", "image_url": {"url": "https://a/1.png"}},
            {"type": "image_url", "image_url": {"url": PNG_DATA_URL}},
        ]},
    ])
    parts = contents[0]["parts"]
    assert parts[0] == {"text": "看这个"}
    assert parts[1] == {"fileData": {"fileUri": "https://a/1.png"}}
    assert parts[2]["inlineData"]["mimeType"] == "image/png"
    assert parts[2]["inlineData"]["data"] == PNG_B64


def test_gemini_plain_string_content_still_works():
    contents, _system = gemini_normalize([{"role": "user", "content": "你好"}])
    assert contents[0]["parts"] == [{"text": "你好"}]


# ---------- chat 侧门控 ----------


class _Cfg(dict):
    """LLM 配置替身：``_run`` 会调用 ``set_session`` / ``clear_session``。"""

    def set_session(self, session_id):
        self["_session"] = session_id

    def clear_session(self):
        self.pop("_session", None)


def _req_with_images(*, modalities, config=None, text="看看", images=None):
    event = _event([("text", {"text": text}), ("image", {"url": "https://a/1.png"})])
    return PromptRequest(
        config=config or _Cfg(),
        event=event,
        session_id="group_778",
        user_text=text,
        user_images=images if images is not None else [{"kind": "url", "value": "https://a/1.png"}],
        modalities=modalities,
    )


def test_place_images_replaces_last_user_message():
    req = _req_with_images(modalities=["text", "image"], text="看看")
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "看看"}]
    out = place_images(messages, req)
    assert isinstance(out[-1]["content"], list)
    assert out[-1]["content"][1]["type"] == "image_url"


def test_place_images_skipped_for_text_only_model():
    req = _req_with_images(modalities=["text", "tool_use"], text="[图片]")
    messages = [{"role": "user", "content": "[图片]"}]
    out = place_images(messages, req)
    assert out[-1]["content"] == "[图片]"  # 文本模型语义完全不变


def test_place_images_skipped_when_disabled():
    req = _req_with_images(
        modalities=["text", "image"],
        config=_Cfg({"image_understanding_enable": False}),
        text="[图片]",
    )
    messages = [{"role": "user", "content": "[图片]"}]
    out = place_images(messages, req)
    assert out[-1]["content"] == "[图片]"


def test_place_images_without_resolved_images_is_noop():
    req = _req_with_images(modalities=["text", "image"], text="只有文字", images=[])
    messages = [{"role": "user", "content": "只有文字"}]
    out = place_images(messages, req)
    assert out[-1]["content"] == "只有文字"


@pytest.mark.parametrize("modalities", [None, ["text", "image"], ["text", "image", "audio", "tool_use"]])
def test_place_images_works_for_permissive_and_explicit_modalities(modalities):
    req = _req_with_images(modalities=modalities, text="看图")
    messages = [{"role": "user", "content": "看图"}]
    out = place_images(messages, req)
    assert isinstance(out[-1]["content"], list)


def test_sanitize_is_the_last_line_of_defense_for_unsupported_models():
    """清洗永远是最后一步兜底：万一有图块漏进 messages，文本模型也只会看到 [Image]。

    ``place_images`` 已有模态门控（文本模型根本不加图块），这里验证兜底仍然有效。
    """
    from app.llm.assembly import sanitize

    req = _req_with_images(modalities=["text"], text="看看")
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": "看看"},
            {"type": "image_url", "image_url": {"url": "https://a/1.png"}},
        ],
    }]
    out = sanitize(messages, req)
    assert out[-1]["content"] == [
        {"type": "text", "text": "看看"},
        {"type": "text", "text": "[Image]"},
    ]


def test_build_messages_keeps_images_for_image_capable_model():
    """声明了 image 模态的模型拿到的是真图块（不是 [Image] 占位）。"""
    from app.llm.assembly import build_messages

    req = _req_with_images(modalities=["text", "image"], text="看看")
    messages = build_messages(req)
    content = messages[-1]["content"]
    assert isinstance(content, list)
    assert content[1] == {"type": "image_url", "image_url": {"url": "https://a/1.png"}}


# ---------- 流水线门控：@我 + 只发图 不能被整轮丢弃 ----------


class _FakeRuntime:
    bot_id = 3569937952

    def __init__(self, config=None, chain=None):
        self.config = _Cfg(config or {"group_enable": True, "trigger_at": True, "image_understanding_enable": True})
        self.chain = chain or [{"provider": "openai", "modalities": ["text", "image"]}]

    def provider_config(self):
        return {"api_key": "sk-test"}

    def provider_chain(self):
        return self.chain


class _BoomLock:
    """在进入 LLM 请求前中断流程（本用例只覆盖到图片解析为止）。"""

    async def acquire(self):
        raise RuntimeError("test-stop")

    def locked(self):
        return False

    def release(self):
        pass


class _BoomLocks:
    def lock(self, session_id):
        return _BoomLock()


async def test_pipeline_lets_image_only_at_message_through(monkeypatch):
    """回归：此前 ``if not ctx.user_text: return`` 会把「@我 + 只发图」整轮丢掉。"""
    from app.llm.context import LlmContext, LlmJob
    from app.llm.pipeline import LlmPipeline

    runtime = _FakeRuntime()
    pipeline = LlmPipeline(runtime)
    pipeline.session_locks = _BoomLocks()
    event = _event([("at", {"qq": "3569937952"}), ("image", {"url": "https://a/1.png"})])
    stages: list[str] = []

    async def _continue(_job, debounce=0.0):
        return True  # 让流程继续，直到 pre_request

    async def _record_stage(stage, ctx, *args, **kwargs):
        stages.append(stage)
        return True

    monkeypatch.setattr(pipeline.pool, "wait_for_continue", _continue)
    monkeypatch.setattr(pipeline, "_run_stage", _record_stage)

    ctx = LlmContext(event=event, runtime=runtime, bot=None, session_id="group_778", user_text="")
    ctx.job = LlmJob(id="t", group_key="group_778", ctx=ctx, generation=0)
    await pipeline._run(ctx.job)

    assert stages == ["pre_request"]          # 没有被 `if not ctx.user_text: return` 拦下
    assert ctx.state.get("has_image") is True
    assert ctx.state.get("user_images") == [{"kind": "url", "value": "https://a/1.png"}]


async def test_pipeline_resolve_images_skips_text_only_model(monkeypatch):
    from app.llm.context import LlmContext
    from app.llm.pipeline import LlmPipeline

    runtime = _FakeRuntime(chain=[{"provider": "openai", "modalities": ["text", "tool_use"]}])
    pipeline = LlmPipeline(runtime)
    event = _event([("image", {"url": "https://a/1.png"})])
    ctx = LlmContext(event=event, runtime=runtime, bot=None, session_id="group_778", user_text="")

    assert await pipeline._resolve_images(ctx) == []


async def test_pipeline_resolve_images_disabled_by_config():
    from app.llm.context import LlmContext
    from app.llm.pipeline import LlmPipeline

    runtime = _FakeRuntime(config={"image_understanding_enable": False})
    pipeline = LlmPipeline(runtime)
    event = _event([("image", {"url": "https://a/1.png"})])
    ctx = LlmContext(event=event, runtime=runtime, bot=None, session_id="group_778", user_text="")

    assert await pipeline._resolve_images(ctx) == []
