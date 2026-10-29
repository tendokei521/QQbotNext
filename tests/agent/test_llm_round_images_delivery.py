"""``expand_image`` 在**工具轮中途**取回的图片，必须当轮就送给模型。

审查发现的缺陷：``ToolContext.extra["round_images"]`` 只在**请求开始时**被抽走一次
（``stream_response`` / ``generate_response`` 组装完 messages 后立刻 drain），
而工具轮里 ``expand_image`` 是在请求**之后**才往队列里塞图片的 —— 于是：

- 流式：工具轮结束后进入第二轮请求，图片仍留在队列里，本轮模型只能凭回执文字瞎猜；
- 非流式：工具循环整个跑在 provider 内部，chat.py 连中间态都拿不到，图片永远送不达；
  （P1 把循环收口到 `loop/tool_step` 后，非流式与流式走同一处补图，适配器只发单次请求）
- 而工具回执却写着「下一轮对话里你会直接看到它」→ 模型照着回执编造画面描述。

本文件走**真实的** ``stream_response`` / ``generate_response`` 路径（真 ToolContext、
真 expand_image 工具、真消息装配），只把 provider 换成一个按脚本产出的探针，
断言第二轮的请求消息里确实带上了图片块。
"""

from __future__ import annotations

from types import SimpleNamespace as _SN  # noqa: E402


def _install_context_tools(runtime):
    """模拟「llm_context 模块已安装」：把它提供的工具源挂到运行时。

    v3 起 `expand_*` 由模块的 tool_source 提供，框架不再注入。
    """
    from app.agent.loop.providers import register_tool_source
    from app.plugins.llm_context.src.service import ContextToolSource

    runtime.tool_sources = []
    register_tool_source(runtime, ContextToolSource(_SN(module_name="llm_context")))
    return runtime

import types

import pytest

from app.agent.loop import chat
from app.agent.media import image as image_mod
from app.agent.media import store as image_media
from app.agent.providers import PROVIDERS
from app.agent.providers.base import BaseProvider, LLMResponse, StreamEvent

CALLS: list[list[dict]] = []

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 32

TOOL_CALL = {
    "index": 0,
    "id": "call_img_1",
    "type": "function",
    "function": {"name": "expand_image", "arguments": '{"messages": ["1003"]}'},
}


def _has_image_block(messages: list[dict]) -> bool:
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list) and any(
            isinstance(b, dict) and b.get("type") == "image_url" for b in content
        ):
            return True
    return False


class _Bot:
    """够用的 Bot 替身：expand_image 只需要 get_msg 返回带图片段的消息。"""

    async def get_msg(self, message_id):
        return {
            "status": "ok",
            "data": {
                "message_id": message_id,
                "sender": {"user_id": 20002, "nickname": "小明", "card": ""},
                "message": [
                    {"type": "image", "data": {"url": "https://cdn.example/cat.png"}},
                ],
            },
        }


class _Probe(BaseProvider):
    """第一轮要 expand_image（并真的执行它），第二轮才开口。"""

    async def chat_stream(self, messages, **kwargs):
        CALLS.append([dict(m) for m in messages])
        if len(CALLS) == 1:
            yield StreamEvent(type="tool_call", tool_call=dict(TOOL_CALL))
            yield StreamEvent(type="done", finish_reason="tool_calls")
            return
        yield StreamEvent(type="text", text="图里是只猫。")
        yield StreamEvent(type="done")

    async def chat(self, messages, **kwargs):
        """非流式适配器只发一次请求：要工具就把 tool_calls 交出去（P1 收口后的契约）。

        循环、执行与补图都在 `loop/tool_step.run_blocking_step` 里，适配器不再自己跑。
        """
        CALLS.append([dict(m) for m in messages])
        if len(CALLS) == 1:
            return LLMResponse(
                text="",
                raw={},
                tool_calls=[{k: v for k, v in TOOL_CALL.items() if k != "index"}],
            )
        return LLMResponse(text="图里是只猫。", raw={})


class _Skills:
    def prompt_blocks(self):
        return []


class _Telemetry:
    def record_call_simple(self, **kwargs):
        return None

    def record_tool(self, *args, **kwargs):
        return None


@pytest.fixture(autouse=True)
def _reset():
    CALLS.clear()
    yield
    CALLS.clear()


@pytest.fixture
def probe(monkeypatch, tmp_path):
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    monkeypatch.setitem(PROVIDERS, "img_probe", _Probe)

    async def _download(url, *, max_bytes, timeout=0):
        # 图片现在先落盘再内联：下载换成固定 PNG 字节，测试不联网
        return PNG_BYTES, image_media.sniff_mime(PNG_BYTES)

    monkeypatch.setattr(image_mod, "_download_bytes", _download)


def _runtime(bot_id: int, config: dict):
    from app.agent.session.manager import SessionManager

    rt = types.SimpleNamespace()
    rt.bot_id = bot_id
    rt.bot = _Bot()
    rt.config = dict(config)
    rt.memory = None
    rt.session_mgr = SessionManager(str(bot_id))
    rt.skills = _Skills()
    rt.llm_tools = None  # 用真实的会话上下文工具（含 expand_image）
    rt.telemetry = _Telemetry()
    rt.provider_config = lambda: {"api_key": "sk-test", "model": "img_probe"}
    rt.provider_chain = lambda: [{
        "provider": "img_probe", "model": "img_probe", "modalities": ["text", "image", "tool_use"],
    }]
    # v3：`expand_image` 归 llm_context 模块（框架不再注入），装上它的工具源
    _install_context_tools(rt)
    return rt


def _event():
    from app.domain.events import GroupMessageEvent, MessageSegment, UserInfo

    return GroupMessageEvent(
        event_type="message_group",
        message_type="group",
        time=1788342260,
        user_id=20002,
        self_id=778,
        message=[MessageSegment("text", {"text": "看看那张图"})],
        user=UserInfo(user_id=20002, nickname="小明"),
        group=types.SimpleNamespace(group_id=466052056, group_name="测试群"),
    )


def _ctx():
    from app.agent.loop.context import LlmContext

    event = _event()
    # 生产路径里连接层把 bot 挂在事件上（_collect_llm_ext 从 event.bot 取）
    event.bot = _Bot()
    ctx = LlmContext(event=event, runtime=None, bot=None, session_id="group_466052056")
    ctx.user_text = "看看那张图"
    ctx.state["user_context"] = {"sent_text": "看看那张图", "sender": "小明(20002)"}
    ctx.state["is_at"] = True
    return ctx


def _config(**over):
    base = {
        "system_prompt": "你是助手",
        "group_enable": True,
        "private_enable": True,
        "meta_sender_style": "off",
        "history_rounds": 50,
        "stream_output": True,
        "max_tool_rounds": 5,
        "max_tokens": 1024,
        "context_expand_enable": True,
        "image_max_mb": 10,
    }
    base.update(over)
    return base


async def test_streaming_delivers_round_images_to_next_round(probe):
    """流式：工具轮结束后，第二轮请求必须带上图片块。"""
    rt = _runtime(5501, _config())
    ctx = _ctx()

    out = [str(item) async for item in chat.stream_response(rt, ctx.event, ctx)]

    assert "猫" in "".join(out)
    assert len(CALLS) == 2, f"应有 2 轮请求，实际 {len(CALLS)}"
    assert not _has_image_block(CALLS[0]), "第一轮还没取图，不该有图片块"
    assert _has_image_block(CALLS[1]), "第二轮请求里必须带图片块（修复前这里是 False）"


async def test_non_stream_delivers_round_images_to_next_round(probe):
    """非流式：工具轮取回的图片必须在**同一轮循环**里递给模型（不再靠补问一轮）。"""
    rt = _runtime(5502, _config(stream_output=False))
    ctx = _ctx()

    out = await chat.generate_response(rt, ctx.event, ctx)

    assert "猫" in str(out)
    assert len(CALLS) == 2, f"应有 2 次请求（工具轮 + 回填轮），实际 {len(CALLS)}"
    assert not _has_image_block(CALLS[0])
    assert _has_image_block(CALLS[1]), "回填轮请求里必须带图片块（P1 前图片从未送达）"


async def test_no_image_round_when_queue_is_empty(probe):
    """没取图就不许多发一次请求（补图轮不能变成无条件加请求）。"""
    rt = _runtime(5503, _config(stream_output=False))
    ctx = _ctx()

    class _NoToolProbe(BaseProvider):
        async def chat(self, messages, **kwargs):
            CALLS.append([dict(m) for m in messages])
            return LLMResponse(text="图里是只猫。", raw={})

    PROVIDERS["img_probe"] = _NoToolProbe
    try:
        out = await chat.generate_response(rt, ctx.event, ctx)
    finally:
        PROVIDERS["img_probe"] = _Probe

    assert "猫" in str(out)
    assert len(CALLS) == 1, f"无取图时只应有 1 次请求，实际 {len(CALLS)}"


