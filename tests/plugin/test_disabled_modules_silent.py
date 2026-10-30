"""已关闭的模块必须静默：不进候选、不写 info 级 skip 日志。

回归背景：账号级关闭的外部模块仍被路由进候选，权限节点对每个模块每分钟写一条
``[skip] reason=<module>:account_disabled``（10 个模块 = 每 60s 一屏），
用户会误以为"出问题了"。关闭是显式状态，只允许 debug 级技术日志。
"""

from __future__ import annotations

import types

from app.plugin.nodes import inbound
from app.plugin.nodes.base import MessageContext
from app.plugin.nodes.inbound import ModulePermissionNode, ModuleRouterNode


class _Registry:
    def __init__(self, modules) -> None:
        self._modules = list(modules)

    def loaded(self):
        return list(self._modules)


class _ConfigService:
    def get_module_config(self, module, account_id=None):
        return {}


def _module(name: str, *, enabled: bool, account_id: str = "123"):
    return types.SimpleNamespace(
        module_name=name,
        subscribe=("message_group",),
        bot_id=account_id,
        authority=types.SimpleNamespace(enabled=enabled),
    )


def _event():
    return types.SimpleNamespace(
        event_type="message_group",
        account_id="123",
        user_id=100,
        group=types.SimpleNamespace(group_id=1),
    )


async def test_disabled_modules_never_enter_candidates():
    enabled = _module("ok_mod", enabled=True)
    disabled = _module("off_mod", enabled=False)
    ctx = MessageContext(event=_event(), state={})

    async def _next():
        return None

    await ModuleRouterNode(_Registry([enabled, disabled])).process(ctx, _next)

    names = [m.module_name for m in ctx.state["candidates"]]
    assert names == ["ok_mod"], "关闭的模块不该进入候选（否则每分钟刷 skip 日志）"


async def test_account_disabled_denial_logs_at_debug_only(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(inbound, "log_skip", lambda *a, **kw: calls.append({"args": a, "kw": kw}))

    node = ModulePermissionNode(
        config_service=_ConfigService(), gateway=None, log=None,
        agent_manager=None, registry=None, responder=None,
    )
    denial = types.SimpleNamespace(
        stage="account", source="module_authority", detail="module=off_mod",
        reason="account_disabled",
    )
    node.access.decide_module = lambda module, event: types.SimpleNamespace(
        first_denial=lambda stages: denial
    )
    ctx = MessageContext(event=_event(), state={"candidates": [_module("off_mod", enabled=False)]})

    async def _next():
        return None

    await node.process(ctx, _next)

    assert len(calls) == 1
    assert calls[0]["kw"].get("level") == "debug", "模块关闭只留 debug，不进用户可见日志"
