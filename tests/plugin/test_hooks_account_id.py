"""账号身份匹配：注册用 account_id、触发用 QQ 号，钩子必须能对上。

回归场景（静默失效，无任何日志）：``loader`` 注册钩子时传的 ``bot_id`` 是
**账号 id**（v4 账号唯一凭据，字符串 ``"bot1"``），而 ``@send_hook`` 触发时传入的
``bot`` 的 ``bot_id`` 是**登录后拿到的 QQ 号**（整数 ``123456``）。两者永远不相等，
于是 group_log 的 ``@send_hook`` 从未触发过。修复口径：QQ 号与 account_id 双键任一命中。
"""

from __future__ import annotations

import json
import sys

import pytest

from app.infrastructure.config.config_service import ConfigService
from app.infrastructure.onebot.client import BotConnection
from app.plugin.base import ServiceAccess
from app.plugin.hooks import (
    ApiHookRegistry,
    BeforeSendHookRegistry,
    EventCompletedHookRegistry,
    LifecycleHookRegistry,
    SendHookRegistry,
)
from app.plugin.registry import ModuleRegistry

PLUGIN_SRC = '''
from app.plugin import BaseModule
from app.plugin.hooks import send_hook

CALLS = []


class Module(BaseModule):
    name = "钩子探针"
    sign = "HookProbe"
    description = "测试用"
    permission = "member"

    @send_hook("*", order=1)
    async def on_send(self, ctx):
        CALLS.append(ctx.message_id)
'''

MANIFEST = json.dumps(
    {"name": "hookprobe", "display_name": "钩子探针", "version": "1.0.0", "api_version": 1},
    ensure_ascii=False,
)

SEND_RESPONSE = {"status": "ok", "data": {"message_id": 42}}


class _AccountBot:
    """同时带 QQ 号（bot_id）与账号 id（account_id）的连接替身。"""

    def __init__(self, qq: int = 123456, account: str = "bot1") -> None:
        self.bot_id = qq
        self.account_id = account
        self.label = "测试账号"


class _RecordingLog:
    """最小日志替身：只关心 warning 次数，避免测试写真实日志文件。"""

    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warning(self, message: str, *args, **kwargs) -> None:
        self.warnings.append(message)

    def add_info(self, *args, **kwargs):
        return self

    def exception(self, *args, **kwargs) -> None:
        return None


# ==================== 各注册表：账号 id 命中 ====================


async def test_send_hook_matches_account_id():
    got = []

    async def handler(ctx):
        got.append(ctx.message_id)

    reg = SendHookRegistry(log=_RecordingLog())
    reg.register(bot_id="bot1", module=object(), handler=handler)

    await reg.run(_AccountBot(account="bot1"), "send_group_msg", {"group_id": 1}, SEND_RESPONSE)

    assert got == [42]


async def test_before_send_and_api_hooks_match_account_id():
    before_seen, api_seen = [], []

    async def before_handler(ctx):
        before_seen.append(ctx.message_type)

    async def api_handler(ctx):
        api_seen.append(ctx.action)

    before_reg = BeforeSendHookRegistry(log=_RecordingLog())
    before_reg.register(bot_id="bot1", module=object(), handler=before_handler)
    api_reg = ApiHookRegistry(log=_RecordingLog())
    api_reg.register(bot_id="bot1", module=object(), handler=api_handler)

    bot = _AccountBot(account="bot1")
    assert await before_reg.run(bot, "send_group_msg", {"group_id": 1}) is True
    await api_reg.run(bot, "send_group_msg", {"group_id": 1}, SEND_RESPONSE)

    assert before_seen == ["group"]
    assert api_seen == ["send_group_msg"]


async def test_lifecycle_hook_matches_account_id():
    got = []

    async def handler(ctx):
        got.append((ctx.state, ctx.account_id))

    reg = LifecycleHookRegistry(log=_RecordingLog())
    reg.register(bot_id="bot1", module=object(), handler=handler)

    await reg.run(_AccountBot(account="bot1"), "connected")

    assert got == [("connected", "bot1")]


async def test_event_completed_hook_matches_account_id():
    got = []

    async def handler(ctx):
        got.append(ctx.duration_ms)

    class _Event:
        bot = _AccountBot(account="bot1")

    reg = EventCompletedHookRegistry(log=_RecordingLog())
    reg.register(bot_id="bot1", module=object(), handler=handler)

    await reg.run(_Event(), duration_ms=2.5)

    assert got == [2.5]


async def test_qq_id_registration_still_matches():
    """兼容旧写法：注册时直接给 QQ 号（整数），触发仍按 bot.bot_id 命中。"""
    got = []

    async def handler(ctx):
        got.append(ctx.message_id)

    reg = SendHookRegistry(log=_RecordingLog())
    reg.register(bot_id=123456, module=object(), handler=handler)

    await reg.run(_AccountBot(qq=123456, account="bot1"), "send_group_msg", {"group_id": 1}, SEND_RESPONSE)

    assert got == [42]


async def test_mismatch_is_logged_once_instead_of_silent():
    """匹配不上时必须留下可排查的日志，且不按每条消息刷屏。"""

    async def handler(ctx):
        pass

    log = _RecordingLog()
    reg = SendHookRegistry(log=log)
    reg.register(bot_id="bot1", module=object(), handler=handler)
    other_bot = _AccountBot(qq=999, account="bot9")

    await reg.run(other_bot, "send_group_msg", {"group_id": 1}, SEND_RESPONSE)
    await reg.run(other_bot, "send_group_msg", {"group_id": 1}, SEND_RESPONSE)

    assert len(log.warnings) == 1, "同一账号应只告警一次，避免每条消息刷屏"
    assert "未匹配" in log.warnings[0]


# ==================== 集成：真实 loader 注册 + 真实 BotConnection 触发 ====================


@pytest.fixture
async def hook_env(container, tmp_path):
    """用 tmp_path 造插件，走真实 ModuleRegistry 加载（loader 注册 account_id）。"""
    builtin = tmp_path / "plugins"
    plugin = builtin / "hookprobe"
    plugin.mkdir(parents=True)
    (plugin / "module.json").write_text(MANIFEST, encoding="utf-8")
    (plugin / "main.py").write_text(PLUGIN_SRC, encoding="utf-8")

    registry = ModuleRegistry(
        plugins_dir=builtin,
        zip_plugins_dir=None,
        config_service=container.get(ConfigService),
        services=container.get(ServiceAccess),
        install_service=None,
    )
    await registry.load_all(bot_id="bot1")
    try:
        yield registry, container.get(ServiceAccess)
    finally:
        await registry.unload(bot_id="bot1")
        for key in [k for k in sys.modules if k.startswith("qqbot_plugin_hookprobe")]:
            del sys.modules[key]


async def test_loaded_plugin_send_hook_fires_for_qq_bot(hook_env):
    """loader 以 bot_id="bot1" 加载，触发时 BotConnection.bot_id 是 QQ 号 → 必须命中。"""
    _registry, services = hook_env
    plugin_module = sys.modules["qqbot_plugin_hookprobe"]
    assert plugin_module.CALLS == [], "加载阶段不应触发发送钩子"

    conn = BotConnection(bot_id=123456)
    conn.account_id = "bot1"
    conn.send_hook_registry = services.send_hooks

    async def fake_outbound(action, params):
        return SEND_RESPONSE

    conn.outbound_hook = fake_outbound

    await conn._send("send_group_msg", {"group_id": 1, "message": "hi"})

    assert plugin_module.CALLS == [42], "注册用的是 account_id、触发用的是 QQ 号，双键匹配后应触发"
