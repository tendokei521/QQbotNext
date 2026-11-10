"""官方机器人输出形态契约：一轮只发一条（被动窗口经不起逐句拆分）。"""

from __future__ import annotations

from types import SimpleNamespace

from app.agent.delivery.output import (
    BlockingOutput,
    StreamOutput,
    _is_qqbot,
    select_default_output,
)


class FakeConfig:
    def __init__(self) -> None:
        self.values = {"stream_output": True, "reply_mode": "chat"}

    def get(self, key, default=None):
        return self.values.get(key, default)


class FakeConfigService:
    def __init__(self, protocol: str) -> None:
        self.protocol = protocol

    def get_bot(self, account_id: str):
        return {"id": account_id, "protocol": self.protocol}


def _runtime(protocol: str) -> SimpleNamespace:
    return SimpleNamespace(
        bot_id="acc1",
        config=FakeConfig(),
        config_service=FakeConfigService(protocol),
        _bot=None,
    )


def test_qqbot_forces_single_blocking_output():
    runtime = _runtime("qqbot")
    assert _is_qqbot(runtime) is True
    strategy = select_default_output(runtime.config, runtime)
    assert isinstance(strategy, BlockingOutput)
    assert not isinstance(strategy, StreamOutput)


def test_onebot_keeps_stream_output_when_configured():
    runtime = _runtime("onebot")
    strategy = select_default_output(runtime.config, runtime)
    assert isinstance(strategy, StreamOutput)
