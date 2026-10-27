"""R3-4：群内 `#llm off/on/mute/unmute/status` 与 WebUI 写同一份 channel_overrides。

设计原则（docs/permission-dialogue-policy-design.md §5.3）：面板能做的开关，
群里也能做；两边必须落在同一张表上，避免"指令改了面板不变"。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.agent.loop.command_dispatch import (
    _maybe_handle_policy_command,
    _parse_duration_minutes,
    handle_commands,
)


def _module(config_service, bot_id="acc1"):
    return SimpleNamespace(
        bot_id=bot_id,
        ctx=SimpleNamespace(services=SimpleNamespace(config_service=config_service)),
    )


def _event(role="manager"):
    return SimpleNamespace(permission_role=role, is_admin=role in ("group_admin", "group_owner"), user_id=42)


async def _run(module, action, *, role="manager", is_private=False, group_id="111"):
    sent: list[str] = []

    async def send(msg: str) -> None:
        sent.append(msg)

    handled = await _maybe_handle_policy_command(
        module, action,
        is_private=is_private, group_id=group_id,
        event=_event(role), send=send, is_admin=_event(role).is_admin,
    )
    return handled, sent


def test_duration_parser():
    assert _parse_duration_minutes("30m") == 30
    assert _parse_duration_minutes("2h") == 120
    assert _parse_duration_minutes("1d") == 1440
    assert _parse_duration_minutes("45") == 45
    assert _parse_duration_minutes("") == 60
    assert _parse_duration_minutes("abc") is None
    assert _parse_duration_minutes("0m") is None


async def test_unknown_action_is_not_consumed():
    handled, sent = await _run(_module(None), "task")
    assert handled is False
    assert sent == []


async def test_off_and_on_write_channel_override(container, config_service):
    module = _module(config_service)

    handled, sent = await _run(module, "off")
    assert handled is True and "已关闭" in sent[-1]
    row = config_service.get_channel_override("acc1", "agent", "111")
    assert row is not None and row["enabled"] is False

    await _run(module, "on")
    row = config_service.get_channel_override("acc1", "agent", "111")
    assert row is not None and row["enabled"] is True


async def test_mute_and_unmute(container, config_service):
    import time

    module = _module(config_service)
    before = int(time.time())

    await _run(module, "mute 2h")
    row = config_service.get_channel_override("acc1", "agent", "111")
    assert row["muted_until"] >= before + 2 * 3600 - 5

    await _run(module, "unmute")
    # 该群本来没有其它覆盖 → 静音清掉后整行被删（等于"完全恢复继承"）
    assert config_service.get_channel_override("acc1", "agent", "111") is None

    # 已有开关覆盖时，取消静音只清静音、保留开关
    await _run(module, "off")
    await _run(module, "mute 10m")
    await _run(module, "unmute")
    row = config_service.get_channel_override("acc1", "agent", "111")
    assert row is not None and row["enabled"] is False and int(row["muted_until"]) == 0


async def test_mute_bad_duration_replies_usage(container, config_service):
    _, sent = await _run(_module(config_service), "mute 一会儿")
    assert "用法" in sent[-1]


async def test_status_reports_state_and_trigger(container, config_service):
    module = _module(config_service)
    await _run(module, "off")

    _, sent = await _run(module, "status", role="member")
    text = sent[-1]
    assert "已关闭" in text
    assert "来源 channel" in text
    assert "触发" in text


async def test_member_cannot_toggle_but_can_read_status(container, config_service):
    module = _module(config_service)

    handled, sent = await _run(module, "off", role="member")
    assert handled is True
    assert "权限不足" in sent[-1]
    assert config_service.get_channel_override("acc1", "agent", "111") is None

    _, sent = await _run(module, "status", role="member")
    assert "本群状态" in sent[-1]


async def test_private_chat_rejects_group_switches(container, config_service):
    handled, sent = await _run(_module(config_service), "off", is_private=True)
    assert handled is True
    assert "请在群里" in sent[-1]


async def test_llm_help_lists_policy_commands():
    from app.agent.commands import COMMAND_GROUPS

    groups = {key: items for key, _title, items in COMMAND_GROUPS}
    assert "policy" in groups
    commands = [cmd for cmd, _desc in groups["policy"]]
    for expected in ("#llm off", "#llm on", "#llm status"):
        assert expected in commands


async def test_handle_commands_routes_policy_actions(container, config_service):
    """防漂移：`handle_commands` 必须真的把 off 交给策略处理器（不是"未知指令"）。"""
    module = _module(config_service)
    sent: list[str] = []

    class _Bot:
        async def send_group_msg(self, group_id, message):
            sent.append(message)

    event = SimpleNamespace(
        bot=_Bot(),
        permission_role="manager",
        is_admin=True,
        user_id=42,
        message_id=1,
        self_id=1,
        account_id="acc1",
    )
    consumed = await handle_commands(
        module, SimpleNamespace(history=SimpleNamespace()), "group_111", "111", "42",
        "#llm off", True, False, event=event,
    )

    assert consumed is True
    assert any("已关闭" in str(item) for item in sent), sent
    assert config_service.get_channel_override("acc1", "agent", "111")["enabled"] is False
