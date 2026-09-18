"""工具调用共享处理（tool_loop）回归测试：并行执行 + 参数失败不静默。

历史故障一：一轮内的多个 tool_call 是**串行** ``await``，而单个工具有 20s 超时。
模型一次展开多个 id（“补全消息环境”场景的常态）时最坏 3×20s，直接撞上 Provider
默认 30s 的请求超时——工具都执行完了，整轮请求却已失败。

历史故障二：``arguments`` 解析失败会退化成 ``args={}`` 照样执行，工具收到空参后返回
``error: ...`` 回传给模型，模型只会得到“这工具不好用”的错误结论而放弃调用。
"""

from __future__ import annotations

import json

from app.llm.tool_loop import normalize_and_execute_tool_calls


def _call(name: str, arguments, call_id: str | None = "call_1") -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


# ---------- 并行执行 ----------


async def test_multiple_tool_calls_run_concurrently():
    """三个工具必须**同时**处于执行中；串行执行时每个工具只能看到自己。"""
    import asyncio

    started: list[str] = []

    async def _executor(name, args):
        started.append(name)
        # 并发执行时三个工具都能等到 started 变成 3；串行时只有自己（超时退出）
        for _ in range(60):
            if len(started) == 3:
                break
            await asyncio.sleep(0.01)
        return f"{name}:{len(started)}"

    tool_results: list[dict] = []
    normalized, messages = await normalize_and_execute_tool_calls(
        [_call("a", "{}"), _call("b", "{}"), _call("c", "{}")],
        _executor,
        tool_results,
    )

    assert [m["content"] for m in messages] == ["a:3", "b:3", "c:3"]
    assert len(normalized) == 3
    assert [r["name"] for r in tool_results] == ["a", "b", "c"]


async def test_results_keep_tool_call_order():
    """并行执行后回填顺序必须与 tool_call 顺序一致（tool_call_id 一一对应）。"""
    import asyncio

    delays = {"slow": 0.05, "fast": 0.0}

    async def _executor(name, args):
        await asyncio.sleep(delays[name])
        return f"{name}-done"

    tool_results: list[dict] = []
    normalized, messages = await normalize_and_execute_tool_calls(
        [_call("slow", "{}", "id_slow"), _call("fast", "{}", "id_fast")],
        _executor,
        tool_results,
    )

    assert [m["tool_call_id"] for m in messages] == ["id_slow", "id_fast"]
    assert [m["content"] for m in messages] == ["slow-done", "fast-done"]
    assert [c["id"] for c in normalized] == ["id_slow", "id_fast"]


# ---------- 参数解析：合法路径 ----------


async def test_empty_arguments_string_runs_with_empty_dict():
    """无参数工具（get_current_session / get_chat_history）必须正常执行。"""
    seen: list[tuple] = []

    async def _executor(name, args):
        seen.append((name, args))
        return "ok"

    _, messages = await normalize_and_execute_tool_calls(
        [_call("get_current_session", "")], _executor, []
    )

    assert seen == [("get_current_session", {})]
    assert messages[0]["content"] == "ok"


async def test_dict_arguments_are_wire_encoded_as_json_string():
    seen: list[dict] = []

    async def _executor(name, args):
        seen.append(args)
        return "ok"

    normalized, _ = await normalize_and_execute_tool_calls(
        [_call("expand_context", {"users": [123]})], _executor, []
    )

    assert seen == [{"users": [123]}]
    wire = normalized[0]["function"]["arguments"]
    assert isinstance(wire, str)
    assert json.loads(wire) == {"users": [123]}


# ---------- 参数解析：失败必须显式报错且不执行 ----------


async def test_invalid_json_arguments_do_not_run_tool():
    """非法 JSON 参数不得再退化成“空参硬执行”，且要回传可纠正的错误。"""
    called: list[str] = []

    async def _executor(name, args):
        called.append(name)
        return "不该被调用"

    tool_results: list[dict] = []
    normalized, messages = await normalize_and_execute_tool_calls(
        [_call("expand_context", '{"users": [')], _executor, tool_results
    )

    assert called == []
    assert messages[0]["content"].startswith("error: 工具 expand_context 调用参数不合法")
    assert "合法 JSON" in messages[0]["content"]
    assert tool_results[0]["result"] == messages[0]["content"]
    # 线上 assistant 消息里的 arguments 仍必须是**合法 JSON 字符串**，否则 round-2 请求 400
    assert json.loads(normalized[0]["function"]["arguments"]) == {}


async def test_non_object_json_arguments_are_rejected():
    called: list[str] = []

    async def _executor(name, args):
        called.append(name)
        return "不该被调用"

    _, messages = await normalize_and_execute_tool_calls(
        [_call("expand_context", "[1, 2]")], _executor, []
    )

    assert called == []
    assert "必须是 JSON 对象" in messages[0]["content"]


async def test_parse_failure_does_not_block_sibling_calls():
    """一个工具参数坏掉，不能影响同一轮里其它工具的执行。"""
    called: list[str] = []

    async def _executor(name, args):
        called.append(name)
        return "ok"

    _, messages = await normalize_and_execute_tool_calls(
        [_call("bad", "{"), _call("good", "{}", "call_2")], _executor, []
    )

    assert called == ["good"]
    assert messages[0]["content"].startswith("error:")
    assert messages[1]["content"] == "ok"


# ---------- id 自洽 / 异常兜底 ----------


async def test_missing_id_gets_synthetic_id_matching_tool_call():
    async def _executor(name, args):
        return "ok"

    normalized, messages = await normalize_and_execute_tool_calls(
        [_call("t", "{}", None), _call("t2", "{}", "")], _executor, []
    )

    assert normalized[0]["id"] == "call_0"
    assert normalized[1]["id"] == "call_1"
    assert [m["tool_call_id"] for m in messages] == ["call_0", "call_1"]


async def test_executor_exception_becomes_error_result():
    async def _executor(name, args):
        raise ValueError("连接已断开")

    _, messages = await normalize_and_execute_tool_calls([_call("t", "{}")], _executor, [])

    assert messages[0]["content"].startswith("error: 工具执行异常")
    assert "连接已断开" in messages[0]["content"]


async def test_missing_executor_returns_placeholder():
    _, messages = await normalize_and_execute_tool_calls([_call("t", "{}")], None, [])

    assert messages[0]["content"] == "工具执行器不可用"


async def test_non_str_result_is_stringified():
    async def _executor(name, args):
        return {"status": "ok"}

    _, messages = await normalize_and_execute_tool_calls([_call("t", "{}")], _executor, [])

    assert messages[0]["content"] == "{'status': 'ok'}"


# ---------- 「回应要求」拼在工具结果末尾（T1） ----------


class _BadCfg(dict):
    def get(self, key, default=None):
        raise RuntimeError("配置后端异常")


def test_reply_directive_default_and_switch():
    from app.llm.tool_loop import DEFAULT_REPLY_DIRECTIVE, reply_directive

    assert reply_directive(None) == ""
    assert reply_directive({}) == DEFAULT_REPLY_DIRECTIVE          # 空=用内置默认
    assert reply_directive({"tool_result_directive": "简短点"}) == "简短点"
    assert reply_directive({"tool_result_directive_enable": False}) == ""
    assert reply_directive(_BadCfg()) == ""                        # 配置异常按不追加


def test_default_directive_says_short_and_no_report():
    from app.llm.tool_loop import DEFAULT_REPLY_DIRECTIVE

    assert "不要复述" in DEFAULT_REPLY_DIRECTIVE
    assert "一两句就够" in DEFAULT_REPLY_DIRECTIVE
    assert "继续调用工具" in DEFAULT_REPLY_DIRECTIVE  # 保留多段任务的能力


def test_append_reply_directive_skips_errors_and_empty():
    from app.llm.tool_loop import append_reply_directive

    assert append_reply_directive("内容", "") == "内容"
    assert append_reply_directive("内容", "【要求】") == "内容\n\n【要求】"
    assert append_reply_directive("error: 取不到", "【要求】") == "error: 取不到"
    assert append_reply_directive("", "【要求】") == ""


async def test_executor_appends_directive_to_successful_result():
    """工具结果末尾必须带上回应要求（抑制"工具返回后写长串汇报"的落点）。"""
    from types import SimpleNamespace

    from app.llm.tool import ToolContext, ToolSpec, make_executor

    async def _handler(_ctx, _args):
        return "（我翻到了这条消息）三哥(123)：早"

    spec = ToolSpec(name="t", description="", parameters={"type": "object", "properties": {}},
                    handler=_handler, scopes=("*",))
    runtime = SimpleNamespace(bot_id="1", config={}, telemetry=None, llm_tool_call_hooks=None)
    executor = make_executor([spec], ToolContext(runtime=runtime))

    result = await executor("t", {})

    assert result.startswith("（我翻到了这条消息）")
    assert "给你自己看的资料" in result


async def test_executor_respects_disabled_directive():
    from types import SimpleNamespace

    from app.llm.tool import ToolContext, ToolSpec, make_executor

    async def _handler(_ctx, _args):
        return "结果"

    spec = ToolSpec(name="t", description="", parameters={"type": "object", "properties": {}},
                    handler=_handler, scopes=("*",))
    runtime = SimpleNamespace(bot_id="1", config={"tool_result_directive_enable": False},
                              telemetry=None, llm_tool_call_hooks=None)
    executor = make_executor([spec], ToolContext(runtime=runtime))

    assert await executor("t", {}) == "结果"
