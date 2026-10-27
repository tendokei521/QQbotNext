"""P1-2：启动后打印就绪清单（把"群里 @ 没反应"变成可执行的下一步）。

与 `/api/readiness`、首启向导共用同一份 ReadinessService 数据——
本测试只验证"打印逻辑"：差什么就列什么、修完就只打一行就绪。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.agent.runtime import AgentManager
from app.application.bot_service import BotService
from app.bootstrap import print_readiness_checklist
from app.infrastructure.config.config_service import ConfigService
from app.plugin.registry import ModuleRegistry


class _Container:
    def __init__(self, mapping: dict) -> None:
        self._mapping = mapping

    def get(self, service_type):
        return self._mapping[service_type]


class _ConfigService:
    def __init__(self, bots=None) -> None:
        self._bots = bots or []

    def get_bots(self):
        return self._bots


class _Gateway:
    def __init__(self, connections=None) -> None:
        self.connections = connections or {}
        self.reverse_link = SimpleNamespace(listening=lambda _aid: False)


class _BotService:
    def __init__(self, gateway=None) -> None:
        self.gateway = gateway or _Gateway()


class _AgentManager:
    def __init__(self, runtimes=None) -> None:
        self._runtimes = runtimes or {}

    def runtimes(self):
        return self._runtimes


class _Registry:
    def catalog_entries(self):
        return []


def _container(*, bots=None, connections=None, runtimes=None) -> _Container:
    return _Container({
        ConfigService: _ConfigService(bots),
        BotService: _BotService(_Gateway(connections)),
        AgentManager: _AgentManager(runtimes),
        ModuleRegistry: _Registry(),
    })


def _runtime(config: dict):
    return SimpleNamespace(config=SimpleNamespace(raw_config=config))


def test_lists_blockers_with_where_to_fix(capsys):
    print_readiness_checklist(_container())

    out = capsys.readouterr().out
    assert "还没有账号" in out
    assert "/bots" in out
    assert "Agent 未选择模型" in out
    assert "/agent/basic" in out


def test_prints_single_line_when_ready(capsys):
    container = _container(
        bots=[{"id": "acc1", "ws_url": "ws://127.0.0.1:3001/"}],
        connections={"acc1": SimpleNamespace(status="connected", conn_mode="forward")},
        runtimes={"acc1": _runtime({
            "group_enable": True,
            "provider_model_pool": ["m1"],
        })},
    )

    print_readiness_checklist(container)

    out = capsys.readouterr().out
    assert "[就绪]" in out
    assert "以下配置还缺" not in out
