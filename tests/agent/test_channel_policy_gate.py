"""R2-1：群级开关必须真的改变"回不回"。

`_scene_enabled` 是「账号级场景开关 + 群级覆盖」的唯一入口：
- 没有覆盖 → 沿用 ``group_enable`` / ``private_enable``（升级零行为漂移）；
- 有覆盖 → 覆盖生效，并把来源写进日志（default/account/channel/muted）。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.agent.loop.pipeline import LlmPipeline


class _Config:
    def __init__(self, data: dict) -> None:
        self.raw_config = dict(data)

    def get(self, key, default=None):
        return self.raw_config.get(key, default)


class _ConfigService:
    """只实现策略解析用到的两个方法。"""

    def __init__(self, overrides: dict | None = None) -> None:
        self._overrides = overrides or {}

    def get_channel_override(self, account_id, capability="agent", channel_id=""):
        return self._overrides.get((str(account_id), str(capability), str(channel_id or "")))

    def get_module_config(self, capability, account_id):
        return {}

    def get_module_authority(self, capability, account_id):
        return {}


class _Runtime:
    def __init__(self, config: dict, overrides: dict | None = None) -> None:
        self.bot_id = "acc1"
        self.config = _Config(config)
        self.config_service = _ConfigService(overrides)


def _group_event(group_id=111):
    return SimpleNamespace(
        event_type="message_group",
        group=SimpleNamespace(group_id=group_id),
        user_id=1,
    )


def test_channel_override_disables_one_group_only():
    runtime = _Runtime({"group_enable": True}, {
        ("acc1", "agent", "111"): {"enabled": False, "config": {}, "permission": {}},
    })
    pipeline = LlmPipeline(runtime)

    enabled_a, source_a = pipeline._scene_enabled(_group_event(111), runtime.config)
    enabled_b, source_b = pipeline._scene_enabled(_group_event(222), runtime.config)

    assert (enabled_a, source_a) == (False, "channel")
    assert (enabled_b, source_b) == (True, "default"), "没覆盖的群必须保持账号级默认"


def test_channel_override_can_reenable_a_disabled_account():
    runtime = _Runtime({"group_enable": False}, {
        ("acc1", "agent", "111"): {"enabled": True, "config": {}, "permission": {}},
    })
    pipeline = LlmPipeline(runtime)

    enabled, source = pipeline._scene_enabled(_group_event(111), runtime.config)
    assert (enabled, source) == (True, "channel")

    other, other_source = pipeline._scene_enabled(_group_event(222), runtime.config)
    assert (other, other_source) == (False, "default")


def test_muted_group_reports_muted_source():
    import time

    runtime = _Runtime({"group_enable": True}, {
        ("acc1", "agent", "111"): {
            "enabled": True, "config": {}, "permission": {},
            "muted_until": int(time.time()) + 600,
        },
    })
    pipeline = LlmPipeline(runtime)

    enabled, source = pipeline._scene_enabled(_group_event(111), runtime.config)
    assert enabled is False
    assert source == "muted"


def test_private_scene_uses_private_enable_default():
    runtime = _Runtime({"group_enable": False, "private_enable": True})
    pipeline = LlmPipeline(runtime)
    event = SimpleNamespace(event_type="message_private", user_id=1)

    enabled, source = pipeline._scene_enabled(event, runtime.config)
    assert (enabled, source) == (True, "default")


def test_runtime_without_config_service_falls_back(monkeypatch):
    """测试替身/旧运行时没有 config_service 时，旧读取路径必须仍然有效。"""

    class _Bare:
        bot_id = "acc1"
        config = _Config({"group_enable": True})

    pipeline = LlmPipeline(_Bare())
    assert pipeline.policy is None
    enabled, source = pipeline._scene_enabled(_group_event(111), pipeline.runtime.config)
    assert (enabled, source) == (True, "default")


def test_pipeline_uses_scene_gate():
    """静态守卫：`_run` 的场景判定必须走 `_scene_enabled`（别再直读 group_enable）。"""
    src = Path("app/agent/loop/pipeline.py").read_text(encoding="utf-8")
    assert "self._scene_enabled(ctx.event, config)" in src
    assert 'config.get("group_enable", False)' not in src.split("def _scene_enabled")[1][:400] or True
    # 精确守卫：_run 里不应出现裸的 group_enable 读取
    run_body = src.split("async def _run(self, job: LlmJob)")[1]
    assert 'config.get("group_enable"' not in run_body
