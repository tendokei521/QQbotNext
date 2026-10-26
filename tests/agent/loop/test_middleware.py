"""Q7：中间件 waterfall（order / 改写 / 拦截 / 失败策略 / 卸载）。

三个钩点都在"具体一步"上，而不是整体接管：

- `tool_call.before`：派发前改 `{name, args, call_id}`；
- `tool_result.after`：结果回来后改 `{name, call_id, result_text, is_error, code}`；
- `messages.before_send`：请求前改最终 messages（接在 `apply_post_assemble` 末尾）。

这里不依赖真实 provider：直接调 `normalize_and_execute_tool_calls` 与
`apply_messages_before_send`，断言"可读可替换"的语义。没有中间件时必须**零行为变化**。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.agent.loop.middleware import (
    POINT_MESSAGES_BEFORE_SEND,
    POINT_TOOL_CALL_BEFORE,
    POINT_TOOL_RESULT_AFTER,
    Middleware,
    apply_messages_before_send,
    register_middleware,
    run_point,
    unregister_middleware,
)
from app.agent.loop.tool_loop import normalize_and_execute_tool_calls


def _runtime():
    return SimpleNamespace(bot_id="10001")


def _calls(name: str = "probe", args: str = '{"x": 1}') -> list[dict]:
    return [{
        "id": "c0", "type": "function",
        "function": {"name": name, "arguments": args},
    }]


async def _executor(seen: list | None = None):
    async def _run(name: str, args: dict) -> str:
        if seen is not None:
            seen.append((name, args))
        return f"结果是 {args.get('x')}"

    return _run


# ── 注册表 ────────────────────────────────────────────


def test_order_is_ascending_and_stable():
    runtime = _runtime()
    trail: list[str] = []

    def _maker(tag: str):
        def _handler(payload, *, runtime=None, ctx=None):
            trail.append(tag)
            return None

        return _handler

    register_middleware(runtime, Middleware(point=POINT_TOOL_CALL_BEFORE, order=200, handler=_maker("c"), source="t"))
    register_middleware(runtime, Middleware(point=POINT_TOOL_CALL_BEFORE, order=100, handler=_maker("a"), source="t"))
    register_middleware(runtime, Middleware(point=POINT_TOOL_CALL_BEFORE, order=100, handler=_maker("b"), source="t"))

    items = runtime.middlewares.get(POINT_TOOL_CALL_BEFORE)

    assert [mw.order for mw in items] == [100, 100, 200]


async def test_unregister_by_source_removes_only_that_source():
    runtime = _runtime()

    async def _handler(payload, *, runtime=None, ctx=None):
        return None

    register_middleware(runtime, Middleware(point=POINT_TOOL_CALL_BEFORE, handler=_handler, source="a"))
    register_middleware(runtime, Middleware(point=POINT_TOOL_CALL_BEFORE, handler=_handler, source="b"))

    removed = unregister_middleware(runtime, source="a")

    assert removed == 1
    assert [mw.source for mw in runtime.middlewares.get(POINT_TOOL_CALL_BEFORE)] == ["b"]


# ── tool_call.before / tool_result.after ──────────────


async def test_tool_call_before_can_rewrite_arguments():
    runtime = _runtime()
    seen: list[tuple[str, dict]] = []

    async def _rewrite(payload, *, runtime=None, ctx=None):
        return {"args": {"x": 42}}

    register_middleware(runtime, Middleware(point=POINT_TOOL_CALL_BEFORE, handler=_rewrite, source="t"))
    executor = await _executor(seen)

    _, messages = await normalize_and_execute_tool_calls(
        _calls(), executor, [], runtime=runtime
    )

    assert seen == [("probe", {"x": 42})], "执行器必须收到改后的参数"
    assert "结果是 42" in messages[0]["content"]


async def test_tool_result_after_can_rewrite_the_text():
    runtime = _runtime()

    async def _mask(payload, *, runtime=None, ctx=None):
        return {"result_text": "[已打码]"}

    register_middleware(runtime, Middleware(point=POINT_TOOL_RESULT_AFTER, handler=_mask, source="t"))
    executor = await _executor()

    normalized, messages = await normalize_and_execute_tool_calls(
        _calls(), executor, [], runtime=runtime
    )

    assert messages[0]["content"] == "[已打码]"
    assert normalized[0]["function"]["arguments"] == '{"x": 1}'


async def test_no_middleware_means_no_change():
    runtime = _runtime()
    executor = await _executor()

    _, messages = await normalize_and_execute_tool_calls(_calls(), executor, [], runtime=runtime)

    assert messages[0]["content"] == "结果是 1"


# ── messages.before_send ──────────────────────────────


async def test_messages_before_send_can_append_a_block():
    runtime = _runtime()
    messages = [{"role": "user", "content": "hi"}]

    async def _append(payload, *, runtime=None, ctx=None):
        return {"messages": list(payload["messages"]) + [{"role": "system", "content": "注入"}]}

    register_middleware(runtime, Middleware(point=POINT_MESSAGES_BEFORE_SEND, handler=_append, source="t"))

    out = await apply_messages_before_send(runtime, messages)

    assert out[-1]["content"] == "注入"
    assert messages == [{"role": "user", "content": "hi"}], "不得原地修改调用方的列表"


async def test_messages_before_send_without_middleware_returns_input():
    runtime = _runtime()
    messages = [{"role": "user", "content": "hi"}]

    assert await apply_messages_before_send(runtime, messages) is messages


# ── 拦截与失败策略 ────────────────────────────────────


async def test_abort_stops_the_point():
    runtime = _runtime()

    async def _block(payload, *, runtime=None, ctx=None):
        return {"abort": "策略不允许"}

    register_middleware(runtime, Middleware(point=POINT_TOOL_CALL_BEFORE, handler=_block, source="t"))

    assert await run_point(runtime, POINT_TOOL_CALL_BEFORE, {"args": {}}) is None


async def test_fail_closed_aborts_but_fail_open_continues():
    runtime = _runtime()
    trail: list[str] = []

    async def _boom(payload, *, runtime=None, ctx=None):
        raise RuntimeError("handler 挂了")

    async def _after(payload, *, runtime=None, ctx=None):
        trail.append("after")
        return None

    register_middleware(runtime, Middleware(
        point=POINT_TOOL_CALL_BEFORE, order=10, handler=_boom, fail_closed=True, source="t"
    ))
    assert await run_point(runtime, POINT_TOOL_CALL_BEFORE, {"args": {}}) is None

    unregister_middleware(runtime, source="t")
    register_middleware(runtime, Middleware(
        point=POINT_TOOL_CALL_BEFORE, order=10, handler=_boom, fail_closed=False, source="t"
    ))
    register_middleware(runtime, Middleware(
        point=POINT_TOOL_CALL_BEFORE, order=20, handler=_after, source="t"
    ))

    out = await run_point(runtime, POINT_TOOL_CALL_BEFORE, {"args": {}})

    assert out is not None, "fail_open 必须继续"
    assert trail == ["after"], "后面的中间件仍然要跑"


# ── 顺序 / 幂等 / 零开销 ──────────────────────────────


async def test_handlers_run_in_order_not_just_sorted():
    """不是"排好序"就算数：实际执行轨迹必须是 order 升序、同 order 按注册顺序。"""
    runtime = _runtime()
    trail: list[str] = []

    def _maker(tag: str):
        async def _handler(payload, *, runtime=None, ctx=None):
            trail.append(tag)
            return None

        return _handler

    for order, tag in ((200, "c"), (100, "a"), (100, "b"), (50, "first")):
        register_middleware(runtime, Middleware(
            point=POINT_TOOL_CALL_BEFORE, order=order, handler=_maker(tag), source="t"
        ))

    await run_point(runtime, POINT_TOOL_CALL_BEFORE, {"args": {}})

    assert trail == ["first", "a", "b", "c"]


def test_register_is_idempotent():
    runtime = _runtime()

    async def _handler(payload, *, runtime=None, ctx=None):
        return None

    mw = Middleware(point=POINT_TOOL_CALL_BEFORE, handler=_handler, source="t")
    register_middleware(runtime, mw)
    register_middleware(runtime, mw)
    # 同 source 同 handler 的**新对象**也不算新条目（插件重复 on_load 不该叠 N 份）
    register_middleware(runtime, Middleware(point=POINT_TOOL_CALL_BEFORE, handler=_handler, source="t"))

    assert len(runtime.middlewares) == 1


async def test_no_middleware_returns_input_object_untouched():
    runtime = _runtime()
    payload = {"args": {"x": 1}}

    assert await run_point(runtime, POINT_TOOL_CALL_BEFORE, payload) is payload


async def test_unknown_point_is_ignored():
    runtime = _runtime()

    async def _handler(payload, *, runtime=None, ctx=None):
        return {"abort": "不该被跑到"}

    register_middleware(runtime, Middleware(point="tool_call.typo", handler=_handler, source="t"))

    assert not getattr(runtime, "middlewares", None), "未知钩点不注册（连注册表都不建）"
    assert await run_point(runtime, POINT_TOOL_CALL_BEFORE, {"a": 1}) == {"a": 1}


# ── 拦截 / 失败策略在调用方侧的可感知性 ────────────────


async def test_tool_call_before_abort_blocks_dispatch_and_pairs_a_failed_result():
    runtime = _runtime()
    seen: list[tuple[str, dict]] = []

    async def _block(payload, *, runtime=None, ctx=None):
        return {"abort": "越权参数"}

    register_middleware(runtime, Middleware(point=POINT_TOOL_CALL_BEFORE, handler=_block, source="t"))

    normalized, messages = await normalize_and_execute_tool_calls(
        _calls(), await _executor(seen), [], runtime=runtime
    )

    assert seen == [], "被拦截的调用绝不能派发"
    assert messages[0]["content"].startswith("error:"), "调用方/模型要能感知失败"
    assert messages[0]["tool_call_id"] == normalized[0]["id"] == "c0", "配对不能破"


async def test_fail_closed_exception_blocks_that_call():
    runtime = _runtime()
    seen: list[tuple[str, dict]] = []

    async def _boom(payload, *, runtime=None, ctx=None):
        raise RuntimeError("中间件挂了")

    register_middleware(runtime, Middleware(
        point=POINT_TOOL_CALL_BEFORE, handler=_boom, fail_closed=True, source="t"
    ))

    _, messages = await normalize_and_execute_tool_calls(
        _calls(), await _executor(seen), [], runtime=runtime
    )

    assert seen == [], "fail_closed 抛异常 = 本步失败，工具不得派发"
    assert messages[0]["content"].startswith("error:")


async def test_fail_open_exception_lets_the_tool_run():
    runtime = _runtime()
    seen: list[tuple[str, dict]] = []

    async def _boom(payload, *, runtime=None, ctx=None):
        raise RuntimeError("中间件挂了")

    register_middleware(runtime, Middleware(
        point=POINT_TOOL_CALL_BEFORE, handler=_boom, fail_closed=False, source="t"
    ))

    _, messages = await normalize_and_execute_tool_calls(
        _calls(), await _executor(seen), [], runtime=runtime
    )

    assert seen == [("probe", {"x": 1})], "fail_open 只留痕，工具照常执行"
    assert messages[0]["content"] == "结果是 1"


# ── 改写结果同源 / 卸载后失效 ──────────────────────────


async def test_tool_result_after_rewrites_tool_results_and_message():
    runtime = _runtime()
    tool_results: list[dict] = []

    async def _mask(payload, *, runtime=None, ctx=None):
        return {"result_text": "[已打码]", "is_error": False}

    register_middleware(runtime, Middleware(point=POINT_TOOL_RESULT_AFTER, handler=_mask, source="t"))

    _, messages = await normalize_and_execute_tool_calls(
        _calls(), await _executor(), tool_results, runtime=runtime
    )

    assert messages[0]["content"] == "[已打码]"
    assert tool_results[0]["result"] == "[已打码]", "tool_results 与 tool 消息必须同源"


async def test_unregister_stops_the_effect():
    runtime = _runtime()
    seen: list[tuple[str, dict]] = []

    async def _rewrite(payload, *, runtime=None, ctx=None):
        return {"args": {"x": 9}}

    register_middleware(runtime, Middleware(point=POINT_TOOL_CALL_BEFORE, handler=_rewrite, source="t"))
    await normalize_and_execute_tool_calls(_calls(), await _executor(seen), [], runtime=runtime)
    assert seen[-1] == ("probe", {"x": 9})

    assert unregister_middleware(runtime, source="t") == 1
    await normalize_and_execute_tool_calls(_calls(), await _executor(seen), [], runtime=runtime)
    assert seen[-1] == ("probe", {"x": 1}), "卸载后不得再生效"


# ── messages.before_send 接在请求收口（apply_post_assemble）上 ──


async def test_messages_before_send_is_wired_into_apply_post_assemble():
    from app.agent.assembly.assembler import apply_post_assemble

    runtime = SimpleNamespace(bot_id="10001", config={}, telemetry=None)

    async def _inject(payload, *, runtime=None, ctx=None):
        return {"messages": [{"role": "system", "content": "注入的上下文"}, *payload["messages"]]}

    register_middleware(runtime, Middleware(point=POINT_MESSAGES_BEFORE_SEND, handler=_inject, source="t"))

    out = await apply_post_assemble(runtime, None, [{"role": "user", "content": "hi"}])

    assert out is not None
    assert out[0] == {"role": "system", "content": "注入的上下文"}
    assert out[-1] == {"role": "user", "content": "hi"}


async def test_messages_before_send_abort_skips_the_request():
    from app.agent.assembly.assembler import apply_post_assemble

    runtime = SimpleNamespace(bot_id="10001", config={}, telemetry=None)

    async def _block(payload, *, runtime=None, ctx=None):
        return {"abort": "本轮不许发"}

    register_middleware(runtime, Middleware(point=POINT_MESSAGES_BEFORE_SEND, handler=_block, source="t"))

    assert await apply_post_assemble(runtime, None, [{"role": "user", "content": "hi"}]) is None

