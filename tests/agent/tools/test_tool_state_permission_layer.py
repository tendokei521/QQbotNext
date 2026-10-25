"""`resolve_tool_state` 判定链第 ⑤ 层（权限/作用域）的回归测试。

## 事故二（本文件锁住）

判定链第 ⑤ 层写的是 ``spec.allowed_for(event, scope=scope)``，而 ``ToolSpec`` 上
只有 ``allows()``——方法不存在，异常被 ``except Exception`` 吞掉，
于是 ``REASON_PERMISSION_DENIED`` 分支**从未被执行过**：
界面上永远看不到"当前身份权限不足"这个原因码，执行前复核也从来没做过权限预判。

这里同时锁住"两层判定同一份实现"：注入期（本模块）与执行期（``make_executor``）
对同一个工具、同一个触发者必须给出同一个结论。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.agent.tools.spec import ToolSpec, make_executor
from app.agent.tools.state import REASON_PERMISSION_DENIED, resolve_tool_state


def _runtime():
    config = {"onebot_tools_enable": True, "tools_disabled": []}
    cfg = SimpleNamespace(get=lambda k, d=None: config.get(k, d))
    return SimpleNamespace(
        bot_id="1",
        config=cfg,
        tools_options=SimpleNamespace(get=lambda k, d=None: config.get(k, d)),
        llm_tools=None,
        mcp_service=None,
        telemetry=None,
        llm_tool_call_hooks=None,
    )


def _spec(calls: list | None = None) -> ToolSpec:
    async def _handler(ctx, args):
        if calls is not None:
            calls.append(args)
        return "ok"

    return ToolSpec(
        name="delete_msg",
        description="",
        parameters={"type": "object", "properties": {}},
        handler=_handler,
        permission="group_admin",
        scopes=["group"],
        source="module",
    )


def _event(role: str):
    return SimpleNamespace(
        event_type="message_group",
        message_type="group",
        permission_role=role,
        is_bot_owner=role == "owner",
        group=object(),
        user_id=1,
    )


# ---------- 注入期：判定链第 ⑤ 层 ----------


def test_member_is_denied_with_permission_reason():
    state = resolve_tool_state(_runtime(), _spec(), session_scope="group", event=_event("member"))
    assert state.available is False
    assert state.reason == REASON_PERMISSION_DENIED
    assert "权限" in state.reason_text


def test_group_admin_is_allowed():
    state = resolve_tool_state(_runtime(), _spec(), session_scope="group", event=_event("group_admin"))
    assert state.available is True


def test_no_event_skips_the_permission_layer():
    """无事件场景（主动/定时）没有权限主语：第 ⑤ 层整层跳过。"""
    state = resolve_tool_state(_runtime(), _spec(), session_scope="group")
    assert state.available is True


# ---------- 执行期：与注入期同一份实现 ----------


async def test_execution_gate_denies_before_running_the_tool():
    calls: list = []
    spec = _spec(calls)
    ctx = SimpleNamespace(runtime=_runtime(), event=_event("member"), session_id="group_1")

    out = await make_executor([spec], ctx)("delete_msg", {})

    assert not calls, "被权限拦下的工具不该真的执行"
    assert out.startswith("error:")
    assert "权限" in out


async def test_execution_gate_allows_admin():
    calls: list = []
    spec = _spec(calls)
    ctx = SimpleNamespace(runtime=_runtime(), event=_event("group_admin"), session_id="group_1")

    out = await make_executor([spec], ctx)("delete_msg", {})

    assert calls == [{}]
    assert out.startswith("ok")
