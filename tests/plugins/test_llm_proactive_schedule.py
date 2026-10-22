"""`llm_proactive`（17 键）与 `llm_schedule`（3 键）模块测试。

这两个是"纯业务模块"：框架不再自带 `runtime.proactive` / `runtime.scheduler`，
装上模块才有；配置也归模块。测试验证：

1. 键与分区表一致、schema/default 覆盖；
2. `on_load` 把实现装进运行时且**读的是模块配置**（`RuntimeFacade`）；
3. `on_unload` 干净摘除（含停止计时器）；
4. 未装模块时运行为 None —— 流水线侧不炸。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent.config_partition import MODULE_KEYS
from app.agent.policy import RuntimeFacade, facade_for, policy_get
from app.plugins.llm_proactive.main import OWNED_KEYS as PROACTIVE_KEYS
from app.plugins.llm_proactive.main import Module as ProactiveModule
from app.plugins.llm_schedule.main import OWNED_KEYS as SCHEDULE_KEYS
from app.plugins.llm_schedule.main import Module as ScheduleModule


def test_keys_match_partition():
    assert set(PROACTIVE_KEYS) == set(MODULE_KEYS["llm_proactive"])
    assert set(SCHEDULE_KEYS) == set(MODULE_KEYS["llm_schedule"])


def test_default_config_and_schema_cover_keys():
    assert not [k for k in PROACTIVE_KEYS if k not in ProactiveModule.default_config]
    assert not [k for k in PROACTIVE_KEYS if k not in ProactiveModule.config_schema]
    assert not [k for k in SCHEDULE_KEYS if k not in ScheduleModule.default_config]
    assert not [k for k in SCHEDULE_KEYS if k not in ScheduleModule.config_schema]


def test_modules_are_pure_business():
    """v3：主动消息与定时任务是**业务**，不声明 supersedes。

    它们用基础接口工作：订阅 `message_observed`/`bot_sent` 事实，用
    `spawn` 起计时、`llm_turn` 跑一轮、`send_to` 发出去。
    """
    assert not getattr(ProactiveModule, "supersedes", ())
    assert not getattr(ScheduleModule, "supersedes", ())


# ---------- 门面 ----------


def test_facade_overrides_config_and_delegates_rest():
    runtime = SimpleNamespace(bot_id="1", config={"x": 1}, provider_chain=lambda: ["p"])
    facade = facade_for(runtime, "proactive", {"proactive_max_unanswered": 7}, PROACTIVE_KEYS)

    assert isinstance(facade, RuntimeFacade)
    assert facade.config.get("proactive_max_unanswered") == 7
    assert facade.bot_id == "1", "其余属性应透传"
    assert facade.provider_chain() == ["p"]


def test_facade_falls_back_to_agent_config_for_unowned_keys():
    runtime = SimpleNamespace(bot_id="1", config={"system_prompt": "你是助手"})
    facade = facade_for(runtime, "proactive", {}, PROACTIVE_KEYS)
    assert facade.config.get("system_prompt") == "你是助手"


# ---------- 装/卸：主动消息 ----------


class _TaskManager:
    def create_task(self, coro, name=None, owner="system"):
        import asyncio

        task = asyncio.ensure_future(coro)
        return task


class _Hooks:
    def __init__(self):
        self.registered: list[dict] = []

    def register(self, **kwargs):
        self.registered.append(kwargs)


class _Runtime:
    def __init__(self, tmp_path):
        self.bot_id = "12345"
        self.llm_hooks = _Hooks()
        self.config = {"proactive_max_unanswered": 3}
        self.proactive = None
        self.scheduler = None
        self.proactive_options = None
        self.schedule_options = None
        self.provider_chain = lambda: [{"provider": "openai", "model": "m"}]
        self.provider_config = lambda: {"api_key": "k", "model": "m"}
        self.ctx = SimpleNamespace(services=SimpleNamespace(task_manager=_TaskManager()))
        self.session_mgr = SimpleNamespace(_stub=True)
        self.llm_tools = None
        self.skills = None
        self.memory = None
        self.knowledge = None
        self.mcp_manager = None


class _CfgService:
    def __init__(self, data=None):
        self.data = data or {}

    def get_module_config(self, *a, **k):
        return dict(self.data)

    def set_module_config(self, *a, **k):
        return None

    def get_module_authority(self, *a, **k):
        return {"enabled": True}

    def set_module_authority(self, *a, **k):
        return None


def _make(cls, name, runtime, values=None):
    from app.plugin.base import ModuleAuthority, ModuleConfig, ModuleContext, ServiceAccess

    service = _CfgService(values or {})
    ctx = ModuleContext(
        module_name=name,
        bot_id="12345",
        config=ModuleConfig(name, "12345", cls.default_config, service),
        authority=ModuleAuthority(name, "12345", service),
        services=ServiceAccess(runtime=lambda bot_id: runtime),
    )
    return type("M", (cls,), {})(ctx)


async def test_proactive_module_installs_and_removes(tmp_path, monkeypatch):
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    runtime = _Runtime(tmp_path)
    module = _make(ProactiveModule, "llm_proactive", runtime, {"proactive_max_unanswered": 9})

    await module.on_load()
    assert runtime.proactive_options is not None
    # 订阅了两个广播事实（框架不再调 runtime.proactive）
    stages = {(h["stage"], h["order"]) for h in runtime.llm_hooks.registered}
    assert ("message_observed", 80) in stages and ("bot_sent", 80) in stages
    # 管理器读的是**模块配置**（不是 agent 配置）
    assert runtime.proactive_options.get("proactive_max_unanswered") == 9
    assert module.manager is not None  # 管理器由模块自己持有（不再塞进运行时）

    await module.on_unload()
    assert runtime.proactive_options is None
    assert module.manager is None


async def test_schedule_module_installs_and_removes(tmp_path, monkeypatch):
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    runtime = _Runtime(tmp_path)
    module = _make(ScheduleModule, "llm_schedule", runtime, {"schedule_enable": True})

    await module.on_load()
    assert runtime.scheduler is not None
    assert runtime.schedule_options is not None
    assert module.enabled is True

    await module.on_unload()
    assert runtime.scheduler is None
    assert runtime.schedule_options is None


async def test_modules_tolerate_missing_runtime():
    from app.plugin.base import ModuleAuthority, ModuleConfig, ModuleContext, ServiceAccess

    for cls, name in ((ProactiveModule, "llm_proactive"), (ScheduleModule, "llm_schedule")):
        service = _CfgService()
        ctx = ModuleContext(
            module_name=name,
            bot_id="1",
            config=ModuleConfig(name, "1", {}, service),
            authority=ModuleAuthority(name, "1", service),
            services=ServiceAccess(runtime=lambda bot_id: None),
        )
        module = type("M", (cls,), {})(ctx)
        await module.on_load()
        await module.on_unload()


# ---------- 未装模块时流水线不炸 ----------


async def test_pipeline_tolerates_absent_proactive():
    """`runtime.proactive is None` 时 pipeline 的两个通知点都不该抛。"""
    from app.agent.loop.context import LlmContext, LlmJob
    from app.agent.loop.pipeline import LlmPipeline

    runtime = SimpleNamespace(
        bot_id="1",
        config={"interrupt_enable": False},
        proactive=None,
        output=None,
        llm_hooks=None,
        telemetry=None,
    )
    pipeline = LlmPipeline(runtime)
    ctx = LlmContext(
        event=SimpleNamespace(event_type="message_group", user_id=1, self_id=2),
        runtime=runtime,
        bot=None,
        session_id="group_1",
        job=LlmJob(id="j", group_key="group_1"),
    )
    await pipeline._observe(ctx)  # 不该抛


@pytest.mark.parametrize("key", ["proactive_quiet_hours_start", "schedule_prompt"])
async def test_keys_overridable(tmp_path, monkeypatch, key):
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    runtime = _Runtime(tmp_path)
    runtime.config = {key: "agent"}
    cls = ProactiveModule if key.startswith("proactive") else ScheduleModule
    name = "llm_proactive" if key.startswith("proactive") else "llm_schedule"
    kind = "proactive" if key.startswith("proactive") else "schedule"
    module = _make(cls, name, runtime, {key: "module"})

    await module.on_load()
    assert policy_get(runtime, kind, key) == "module"
    await module.on_unload()
