"""输出策略槽：`runtime.output` 可被整体替换，卸载后回到框架默认。

这是「把流式输出做成模块」的全部前提 —— 在此之前"流式还是非流式"是散落在
3 个文件里的 `config.get("stream_output")` 分支，模块只能改配置或改源码。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent.delivery.output import (
    BlockingOutput,
    StreamOutput,
    resolve_output,
    select_default_output,
)
from app.agent.loop.context import LlmJob
from app.agent.loop.pipeline import LlmPipeline
from app.domain.message import Message


class _Config:
    def __init__(self, **values):
        self._values = values
        self.session = ""

    def get(self, key, default=None):
        return self._values.get(key, default)

    def set_session(self, session_id):
        self.session = session_id

    def clear_session(self):
        self.session = ""


def _runtime(**over):
    base = dict(
        bot_id="1",
        config=_Config(stream_output=False),
        output=None,
        llm_hooks=None,
        telemetry=None,
        proactive=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


# ---------- 默认策略选择 ----------


def test_select_default_output_follows_config():
    assert isinstance(select_default_output(_Config(stream_output=True)), StreamOutput)
    assert isinstance(select_default_output(_Config(stream_output=False)), BlockingOutput)
    # 配置对象异常时保守走非流式
    assert isinstance(select_default_output(None), BlockingOutput)


def test_resolve_output_prefers_runtime_slot():
    class _Custom:
        streams = True

        async def respond(self, ctx, *, should_stop=None):
            yield Message.from_text("custom")

    custom = _Custom()
    runtime = _runtime(output=custom)
    assert resolve_output(runtime) is custom


def test_resolve_output_falls_back_when_unusable():
    runtime = _runtime(output=object())  # 没有 respond
    assert isinstance(resolve_output(runtime), BlockingOutput)


# ---------- 自定义策略真的接管了发送 ----------


class _Bot:
    def __init__(self):
        self.sent: list[str] = []

    async def send_group_msg(self, group_id, message, auto_escape=False):
        self.sent.append(getattr(message, "text", str(message)))
        return {"status": "ok", "data": {"message_id": 1}}

    async def send_private_msg(self, user_id, message, auto_escape=False):
        self.sent.append(getattr(message, "text", str(message)))
        return {"status": "ok", "data": {"message_id": 2}}


async def test_custom_output_strategy_drives_sending(monkeypatch):
    """自定义 respond 产出的消息由流水线逐条发出（模块不需要自己发）。"""
    bot = _Bot()
    events: list[str] = []

    class _Strategy:
        streams = True

        async def respond(self, ctx, *, should_stop=None):
            yield Message.from_text("第一句")
            yield Message.from_text("第二句")

    runtime = _runtime(output=_Strategy(), config=_Config())
    pipeline = LlmPipeline(runtime)
    runtime.llm_pipeline = pipeline
    ctx = SimpleNamespace(
        runtime=runtime,
        event=SimpleNamespace(event_type="message_group", message_type="group",
                              group=SimpleNamespace(group_id=1), user_id=2, reply=None),
        bot=bot,
        session_id="group_1",
        job=LlmJob(id="j", group_key="group_1"),
        state={},
        messages=[],
        response_text="",
        response_messages=[],
    )

    async def _run_stage(stage, _ctx, msg=None):
        events.append(stage)
        return True

    monkeypatch.setattr(pipeline, "_run_stage", _run_stage)
    async def _send(_ctx, msg):
        bot.sent.append(msg.text)

    monkeypatch.setattr(pipeline, "_send", _send)

    await pipeline._drive(ctx, resolve_output(runtime))

    assert bot.sent == ["第一句", "第二句"]
    assert events == [
        "pre_send", "post_send",
        "pre_send", "post_send",
        "post_stream",  # streams=True → 整体结束钩子
    ]


async def test_blocking_strategy_does_not_fire_post_stream(monkeypatch):
    bot = _Bot()
    stages: list[str] = []

    class _Strategy:
        streams = False

        async def respond(self, ctx, *, should_stop=None):
            yield Message.from_text("一次性")

    runtime = _runtime(output=_Strategy())
    pipeline = LlmPipeline(runtime)
    ctx = SimpleNamespace(
        runtime=runtime,
        event=SimpleNamespace(event_type="message_group", group=SimpleNamespace(group_id=1)),
        bot=bot,
        session_id="group_1",
        job=None,
        state={},
    )

    async def _run_stage(stage, _ctx, msg=None):
        stages.append(stage)
        return True

    async def _send(_ctx, msg):
        bot.sent.append(msg.text)

    monkeypatch.setattr(pipeline, "_run_stage", _run_stage)
    monkeypatch.setattr(pipeline, "_send", _send)

    await pipeline._drive(ctx, resolve_output(runtime))

    assert bot.sent == ["一次性"]
    assert "post_stream" not in stages, "非流式不该触发 post_stream"


async def test_pre_send_hook_can_drop_single_message(monkeypatch):
    bot = _Bot()

    class _Strategy:
        streams = False

        async def respond(self, ctx, *, should_stop=None):
            yield Message.from_text("会被拦下")
            yield Message.from_text("这条能过")

    runtime = _runtime(output=_Strategy())
    pipeline = LlmPipeline(runtime)
    ctx = SimpleNamespace(
        runtime=runtime,
        event=SimpleNamespace(event_type="message_group", group=SimpleNamespace(group_id=1)),
        bot=bot,
        session_id="group_1",
        job=None,
        state={},
    )

    async def _run_stage(stage, _ctx, msg=None):
        if stage == "pre_send" and getattr(msg, "text", "") == "会被拦下":
            msg.skip = True
        return True

    async def _send(_ctx, msg):
        bot.sent.append(msg.text)

    monkeypatch.setattr(pipeline, "_run_stage", _run_stage)
    monkeypatch.setattr(pipeline, "_send", _send)

    await pipeline._drive(ctx, resolve_output(runtime))
    assert bot.sent == ["这条能过"]


# ---------- FeatureRegistry 接管与还原 ----------


def test_output_feature_swaps_and_restores():
    from app.plugin.features import FeatureRegistry
    from app.plugin.features.controllers import OutputFeatureController

    class _Manager:
        def __init__(self, runtime):
            self._runtime = runtime

        def get_runtime(self, bot_id):
            return self._runtime

    class _Owner:
        name = "自定义输出"

        async def respond(self, ctx, *, should_stop=None):
            yield Message.from_text("x")

    runtime = _runtime()
    registry = FeatureRegistry()
    registry.register(OutputFeatureController(_Manager(runtime)))
    owner = _Owner()

    assert registry.suppress("output", owner, "1") != {}
    assert runtime.output is owner

    registry.release("output", owner, "1")
    assert runtime.output is None, "释放后必须回到 None（= 框架按配置自动选择）"


def test_output_feature_rejects_owner_without_respond():
    from app.plugin.features import FeatureRegistry
    from app.plugin.features.controllers import OutputFeatureController

    class _Manager:
        def __init__(self, runtime):
            self._runtime = runtime

        def get_runtime(self, bot_id):
            return self._runtime

    runtime = _runtime()
    registry = FeatureRegistry()
    registry.register(OutputFeatureController(_Manager(runtime)))

    registry.suppress("output", SimpleNamespace(name="坏的"), "1")
    assert runtime.output is None, "不合格的接管者不能被装进运行时"


@pytest.mark.parametrize("attr", ["streams"])
def test_strategies_declare_streaming_flag(attr):
    """`_drive` 靠这个标记决定要不要触发 post_stream，必须显式声明。"""
    assert hasattr(BlockingOutput(), attr)
    assert hasattr(StreamOutput(), attr)
    assert BlockingOutput().streams is False
    assert StreamOutput().streams is True
