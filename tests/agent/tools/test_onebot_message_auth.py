"""「消息凭证」鉴权契约（2026-11-03）。

群聊里 high/critical 的 OneBot 工具必须带 ``auth_message_id``；程序用该消息的
发送者身份授权（high=bot 管理者/拥有者，critical=仅拥有者）。锁这些行为：

1. 参数只在群聊的敏感工具上追加（normal 工具、私聊不加）；
2. 缺参/消息不存在/跨群/发送者权限不足 → fail-closed，处理器不执行；
3. owner 消息可授权 high 与 critical；manager 消息只能授权 high；
4. 鉴权参数不透传给 OneBot action。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.agent.tools.onebot.executor import build_onebot_tools
from app.agent.tools.onebot.message_auth import AUTH_ARG, authorize_from_message
from app.agent.tools.spec import ToolContext, make_executor

GROUP_ID = 123
OWNER_ID = 10001
MANAGER_ID = 10002
MEMBER_ID = 20002


class FakeBot:
    bot_id = 1

    def __init__(self, messages):
        self.messages = messages
        self.calls: list[tuple[str, dict]] = []

    async def get_msg(self, message_id):
        return {"data": self.messages.get(int(message_id), {})}

    async def call_api(self, action, params=None):
        self.calls.append((action, params or {}))
        return {"data": {}}


def _runtime():
    config = {
        "onebot_tools_enable": True,
        "onebot_tools_log_enabled": False,
        "onebot_tool_overrides": {},
        "onebot_tools_allowed": [],
        "onebot_tools_denied": [],
        "agent_owner_list": [str(OWNER_ID)],
        "agent_manager_list": [str(MANAGER_ID)],
        "tool_spill_enable": False,
    }
    return SimpleNamespace(
        bot_id=1,
        config=SimpleNamespace(get=lambda key, default=None: config.get(key, default)),
        telemetry=None,
        llm_tool_call_hooks=None,
    )


def _ctx(bot=None, *, private=False) -> ToolContext:
    event = SimpleNamespace(
        event_type="message_private" if private else "message_group",
        message_type="private" if private else "group",
        group=None if private else SimpleNamespace(group_id=GROUP_ID),
        user_id=MEMBER_ID,
        message_id=555,
        permission_role="member",
        is_bot_owner=False,
    )
    return ToolContext(
        runtime=_runtime(),
        event=event,
        session_id="private_1" if private else f"group_{GROUP_ID}",
        bot=bot,
        group_id=None if private else str(GROUP_ID),
    )


def _messages() -> dict:
    return {
        101: {"group_id": GROUP_ID, "sender": {"user_id": OWNER_ID}},
        102: {"group_id": GROUP_ID, "sender": {"user_id": MANAGER_ID}},
        103: {"group_id": GROUP_ID, "sender": {"user_id": MEMBER_ID}},
        104: {"group_id": 999, "sender": {"user_id": OWNER_ID}},
    }


def test_auth_param_only_on_group_sensitive_tools():
    group = _ctx()
    specs = {s.name: s for s in build_onebot_tools(group.runtime, group)}
    kick = specs["set_group_kick"]
    assert kick.self_checked_permission is True
    assert AUTH_ARG in (kick.parameters.get("required") or [])
    assert AUTH_ARG in kick.parameters["properties"]
    normal = specs["send_msg"]
    assert normal.self_checked_permission is False
    assert AUTH_ARG not in (normal.parameters.get("required") or [])

    private = _ctx(private=True)
    private_specs = {s.name: s for s in build_onebot_tools(private.runtime, private)}
    # 私聊不启用消息凭证：私有作用域的 critical 工具仍按触发者判权限
    friend = private_specs["set_friend_add_request"]
    assert friend.self_checked_permission is False
    assert AUTH_ARG not in (friend.parameters.get("required") or [])


async def _authorize(ctx, bot, message_id, sensitivity):
    return await authorize_from_message(
        ctx.runtime,
        bot,
        tool_name="set_group_kick",
        args={AUTH_ARG: message_id},
        ctx=ctx,
        sensitivity=sensitivity,
    )


async def test_message_sender_authorizes_high_and_critical():
    bot = FakeBot(_messages())
    ctx = _ctx(bot=bot)
    assert await _authorize(ctx, bot, 101, "high") == ""
    assert await _authorize(ctx, bot, 101, "critical") == ""
    assert await _authorize(ctx, bot, 102, "high") == ""
    assert await _authorize(ctx, bot, 102, "critical") != ""  # manager 不能授权 critical
    assert await _authorize(ctx, bot, 103, "high") != ""      # 普通成员不能授权 high
    assert await _authorize(ctx, bot, 104, "high") != ""      # 跨群消息不能用
    assert await _authorize(ctx, bot, 999, "high") != ""      # 查不到
    assert await _authorize(ctx, bot, "", "high") != ""       # 缺参


async def test_executor_fails_closed_and_strips_auth_arg():
    bot = FakeBot(_messages())
    ctx = _ctx(bot=bot)
    specs = [s for s in build_onebot_tools(ctx.runtime, ctx) if s.name == "set_group_kick"]
    executor = make_executor(specs, ctx)

    denied = await executor("set_group_kick", {"group_id": GROUP_ID, "user_id": 9, AUTH_ARG: 103})
    assert denied.startswith("error:") and "PERMISSION_DENIED" in denied
    assert bot.calls == [], "鉴权失败时绝不能真的调用 OneBot action"

    allowed = await executor("set_group_kick", {"group_id": GROUP_ID, "user_id": 9, AUTH_ARG: 101})
    assert "PERMISSION_DENIED" not in allowed
    assert bot.calls, "鉴权通过后应执行 action"
    assert AUTH_ARG not in bot.calls[0][1], "鉴权参数不能透传给 OneBot"
