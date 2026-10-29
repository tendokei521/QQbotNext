"""``post_assemble`` 阶段：插件可读写本轮请求 messages。

背景：此前五个 LLM 钩子阶段（pre_request / post_response / pre_send / post_send /
post_stream）**全在组装之外**——pre_request 跑在 ``prepare_prompt`` 之前，
post_response 跑在 provider 返回之后，没有任何一阶段能拿到组装结果。
想做"1:1 复刻第三方组装格式"（例如 AstrBot 的 ``build_main_agent``）只能打猴补丁。

本文件锁住新增的接入点：

1. ``assembly.build_for``：装配器取自 ``runtime.assembler``（可整体替换）；
2. ``assembly.apply_post_assemble``：钩子可原地改写 / 重新赋值 / 置空 ``ctx.messages``；
3. ``hooks.run_stage`` 的统一语义（缺 registry / 缺 event / 钩子抛异常都不炸本轮）；
4. 四条路径共用同一套语义（含没有事件对象的主动性/定时路径）。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.agent.assembly.assembler import PromptAssembler, PromptRequest, apply_post_assemble, build_for
from app.agent.hooks import LlmHookRegistry, run_stage
from app.agent.loop.context import LlmContext, LlmJob


class _Runtime:
    bot_id = 424242

    def __init__(self, hooks=None):
        self.llm_hooks = hooks if hooks is not None else LlmHookRegistry()
        self.config = {"system_prompt": "你是助手", "outbound_directive_enable": False}
        self.telemetry = None


def _req(**kw) -> PromptRequest:
    base = dict(
        config={"system_prompt": "你是助手", "outbound_directive_enable": False},
        session_id="group_1",
        user_text="你好",
        raw_user_text="你好",
        history=[],
        available_tools=set(),
    )
    base.update(kw)
    return PromptRequest(**base)


def _ctx(runtime, event_type: str | None = "message_group") -> LlmContext:
    event = None if event_type is None else SimpleNamespace(event_type=event_type)
    return LlmContext(
        event=event,
        runtime=runtime,
        bot=SimpleNamespace(),
        session_id="group_1",
        user_text="你好",
    )


# ==================== 装配器可替换 ====================


def test_build_for_uses_runtime_assembler():
    """runtime.assembler 生效：插件可整体替换装配器（1:1 复刻的接入点）。"""

    class _FakeAssembler:
        def build(self, req):
            return [{"role": "system", "content": "复刻的组装"}]

    runtime = _Runtime()
    runtime.assembler = _FakeAssembler()
    assert build_for(runtime, _req()) == [{"role": "system", "content": "复刻的组装"}]


def test_build_for_falls_back_when_assembler_unusable():
    """装配器不合格（无 build）时回退默认装配器，不让一个坏装配器炸掉整轮请求。"""
    runtime = _Runtime()
    runtime.assembler = object()
    assert build_for(runtime, _req()) == PromptAssembler().build(_req())


def test_build_for_defaults_without_assembler_attribute():
    """没有 assembler 属性（旧测试替身/旧运行时）时等价于默认装配器。"""
    runtime = _Runtime()
    assert build_for(runtime, _req()) == PromptAssembler().build(_req())


# ==================== 钩子可读写 messages ====================


async def test_post_assemble_can_rewrite_messages():
    runtime = _Runtime()

    async def rewrite(ctx):
        ctx.messages = [{"role": "user", "content": "改写后的请求"}]

    runtime.llm_hooks.register(stage="post_assemble", handler=rewrite)
    result = await apply_post_assemble(runtime, _ctx(runtime), [{"role": "user", "content": "原始"}])
    assert result == [{"role": "user", "content": "改写后的请求"}]


async def test_post_assemble_can_mutate_in_place():
    """原地 append 也要生效（钩子两种写法都支持）。"""
    runtime = _Runtime()

    async def append_system(ctx):
        ctx.messages.append({"role": "system", "content": "<system_reminder>id=1</system_reminder>"})

    runtime.llm_hooks.register(stage="post_assemble", handler=append_system)
    result = await apply_post_assemble(runtime, _ctx(runtime), [{"role": "user", "content": "hi"}])
    assert [m["content"] for m in result] == ["hi", "<system_reminder>id=1</system_reminder>"]


async def test_post_assemble_empty_messages_aborts_request():
    """置空 messages = 本轮不请求 LLM（返回 None，调用方直接 return）。"""
    runtime = _Runtime()

    async def drop(ctx):
        ctx.messages = []

    runtime.llm_hooks.register(stage="post_assemble", handler=drop)
    assert await apply_post_assemble(runtime, _ctx(runtime), [{"role": "user", "content": "hi"}]) is None


async def test_post_assemble_job_skip_aborts_request():
    """钩子标记 job.skip 也视为中止（与其它阶段语义一致）。"""
    runtime = _Runtime()

    async def skip(ctx):
        ctx.job.skip = True

    runtime.llm_hooks.register(stage="post_assemble", handler=skip)
    ctx = _ctx(runtime)
    ctx.job = LlmJob(id="job1", group_key="group_1")
    assert await apply_post_assemble(runtime, ctx, [{"role": "user", "content": "hi"}]) is None


async def test_post_assemble_hook_exception_does_not_break_request():
    """单个钩子抛异常只留痕，不阻断本轮（与其它阶段一致）。"""
    runtime = _Runtime()

    async def boom(ctx):
        raise RuntimeError("钩子炸了")

    runtime.llm_hooks.register(stage="post_assemble", handler=boom)
    messages = [{"role": "user", "content": "hi"}]
    assert await apply_post_assemble(runtime, _ctx(runtime), messages) == messages


async def test_post_assemble_passes_through_when_no_hooks():
    runtime = _Runtime()
    messages = [{"role": "user", "content": "hi"}]
    assert await apply_post_assemble(runtime, _ctx(runtime), messages) == messages


# ==================== 主动性/定时路径：没有事件对象 ====================


async def test_post_assemble_runs_without_event_object():
    """主动性/定时路径没有事件对象（ctx.event is None），钩子仍按 "*" 匹配并生效。"""
    runtime = _Runtime()
    seen = []

    async def hook(ctx):
        seen.append(ctx.event)
        ctx.messages = [{"role": "user", "content": "主动性请求"}]

    runtime.llm_hooks.register(stage="post_assemble", handler=hook)
    result = await apply_post_assemble(runtime, _ctx(runtime, event_type=None), [{"role": "user", "content": "x"}])
    assert seen == [None]
    assert result == [{"role": "user", "content": "主动性请求"}]


async def test_apply_post_assemble_builds_synthetic_ctx():
    """ctx 为 None（无流水线直调）时构造合成上下文，不 AttributeError。"""
    runtime = _Runtime()
    runtime.llm_hooks.register(stage="post_assemble", handler=_noop)
    messages = [{"role": "user", "content": "hi"}]
    assert await apply_post_assemble(runtime, None, messages) == messages


async def _noop(ctx):
    return None


# ==================== run_stage 的统一语义 ====================


async def test_run_stage_tolerates_missing_registry():
    """运行时没有 llm_hooks（旧替身）时视为无钩子，不抛异常。"""
    runtime = SimpleNamespace(bot_id=1)
    assert await run_stage(runtime, "post_assemble", _ctx(runtime)) is True


async def test_run_stage_filters_by_event_type():
    runtime = _Runtime()
    calls = []

    async def group_only(ctx):
        calls.append("group")

    runtime.llm_hooks.register(stage="post_assemble", event_type="message_group", handler=group_only)
    assert await run_stage(runtime, "post_assemble", _ctx(runtime, "message_private")) is True
    assert calls == []
    assert await run_stage(runtime, "post_assemble", _ctx(runtime, "message_group")) is True
    assert calls == ["group"]


