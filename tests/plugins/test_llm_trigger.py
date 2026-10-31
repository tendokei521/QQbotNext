"""`llm_trigger`（主动消息 17 键 + 定时任务 3 键，共 20 键）模块测试。

这是"纯业务模块"：框架不再自带 `runtime.proactive` / `runtime.scheduler`，
装上模块才有；配置也归模块。测试验证：

1. 键与分区表一致、schema/default 覆盖（两份旧键一字未改）；
2. `on_load` 把两套实现都装进运行时且**读的是模块配置**（`ConfigView`）；
3. 两个 `*_options` 视图各自保留原来的 owned 子集（不合成 20 键视图）；
4. `on_unload` 干净摘除（含停止两个计时器）；
5. 未装模块时运行为 None —— 流水线侧不炸。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.agent.policy import RuntimeFacade, facade_for
from app.plugins.llm_trigger.config_schema import SCHEMA
from app.plugins.llm_trigger.main import (
    OWNED_KEYS,
    PROACTIVE_OWNED_KEYS,
    SCHEDULE_OWNED_KEYS,
    Module,
)

# ---------- 键归属 ----------


def test_owned_keys_is_proactive_plus_schedule():
    """合并后 20 键 = 主动 17 + 定时 3，且键名一字不改。"""
    assert len(OWNED_KEYS) == 20
    assert len(PROACTIVE_OWNED_KEYS) == 17
    assert len(SCHEDULE_OWNED_KEYS) == 3
    assert OWNED_KEYS == PROACTIVE_OWNED_KEYS + SCHEDULE_OWNED_KEYS
    assert not set(PROACTIVE_OWNED_KEYS) & set(SCHEDULE_OWNED_KEYS)

    # 旧模块持有的键必须原样保留
    for key in (
        "proactive_friend_enable",
        "proactive_group_enable",
        "stream_proactive_enabled",
        "proactive_friend_sessions",
        "proactive_group_sessions",
        "proactive_min_interval_minutes",
        "proactive_max_interval_minutes",
        "proactive_max_unanswered",
        "proactive_quiet_hours_start",
        "proactive_quiet_hours_end",
        "proactive_group_idle_minutes",
        "proactive_prompt",
        "proactive_prompt_enable",
        "proactive_history_intent_nudge",
        "proactive_env_prompt_enable",
        "proactive_unresolved_prompt_enable",
        "proactive_env_intent_nudge",
        "schedule_enable",
        "schedule_prompt",
        "stream_scheduled_enabled",
    ):
        assert key in OWNED_KEYS


def test_schema_and_defaults_cover_owned_keys():
    """每个持有的键既有 schema（UI 可渲染）也有默认值（未保存时也能跑）。"""
    assert set(SCHEMA) == set(OWNED_KEYS)
    assert set(Module.default_config) == set(OWNED_KEYS)


# ---------- 门面 ----------


def test_facade_overrides_config_and_delegates_rest():
    runtime = SimpleNamespace(bot_id="1", config={"x": 1}, provider_chain=lambda: ["p"])
    facade = facade_for(runtime, "proactive", {"proactive_max_unanswered": 7}, OWNED_KEYS)

    assert isinstance(facade, RuntimeFacade)
    assert facade.config.get("proactive_max_unanswered") == 7
    assert facade.bot_id == "1", "其余属性应透传"
    assert facade.provider_chain() == ["p"]


# ---------- 装/卸 ----------


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


async def test_proactive_side_installs_and_removes(tmp_path, monkeypatch):
    """主动消息半边：装进运行时、订阅两个事实、读模块配置、卸载干净。"""
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    runtime = _Runtime(tmp_path)
    module = _make(Module, "llm_trigger", runtime, {"proactive_max_unanswered": 9})

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


async def test_schedule_side_installs_and_removes(tmp_path, monkeypatch):
    """定时任务半边：调度器 + 工具源 + 配置视图，卸载干净。"""
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    runtime = _Runtime(tmp_path)
    module = _make(Module, "llm_trigger", runtime, {"schedule_enable": True})

    await module.on_load()
    assert runtime.scheduler is not None
    assert runtime.schedule_options is not None
    assert module.enabled is True
    # 工具源（`schedule` 工具）已登记
    assert [s.name for s in getattr(runtime, "tool_sources", [])] == ["schedule"]

    await module.on_unload()
    assert runtime.scheduler is None
    assert runtime.schedule_options is None
    assert module.scheduler is None


async def test_options_views_keep_their_own_owned_subsets(tmp_path, monkeypatch):
    """两个视图各管自己那组键：不合成一个 20 键视图（否则框架侧读键语义会变）。"""
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    runtime = _Runtime(tmp_path)
    module = _make(Module, "llm_trigger", runtime, {})

    await module.on_load()
    try:
        # 视图只"拥有"自己那组键（其余键透传到 agent 配置）
        assert runtime.schedule_options.owns("schedule_enable") is True
        assert runtime.schedule_options.owns("proactive_max_unanswered") is False
        assert runtime.proactive_options.owns("proactive_max_unanswered") is True
        assert runtime.proactive_options.owns("schedule_enable") is False
        assert runtime.proactive_options.get("proactive_max_unanswered") == 3
        assert runtime.schedule_options.get("schedule_enable") is True
    finally:
        await module.on_unload()


# ---------- 未装模块时流水线不炸 ----------


def test_runtime_defaults_are_none():
    """没装模块时运行时上的槽保持 None（流水线已有 getattr 保护）。"""
    runtime = _Runtime(None)
    assert runtime.proactive_options is None
    assert runtime.schedule_options is None
    assert runtime.scheduler is None
