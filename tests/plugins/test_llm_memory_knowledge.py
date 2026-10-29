"""`llm_memory`（17 键）与 `llm_knowledge`（3 键）模块测试。

验证"策略搬走"：把模块值设成与 agent 相反，`MemoryManager.enabled()` /
`KnowledgeManager.enabled()` 必须跟着模块走；卸载后回退。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent.config_partition import MODULE_KEYS
from app.agent.policy import policy_get
from app.plugins.llm_knowledge.main import OWNED_KEYS as KNOWLEDGE_KEYS
from app.plugins.llm_knowledge.main import Module as KnowledgeModule
from app.plugins.llm_memory.main import OWNED_KEYS as MEMORY_KEYS
from app.plugins.llm_memory.main import Module as MemoryModule


def test_memory_keys_match_partition():
    assert set(MEMORY_KEYS) == set(MODULE_KEYS["llm_memory"])


def test_knowledge_keys_match_partition():
    assert set(KNOWLEDGE_KEYS) == set(MODULE_KEYS["llm_knowledge"])


def test_memory_default_config_and_schema_cover_owned_keys():
    assert not [k for k in MEMORY_KEYS if k not in MemoryModule.default_config]
    assert not [k for k in MEMORY_KEYS if k not in MemoryModule.config_schema]


def test_knowledge_default_config_and_schema_cover_owned_keys():
    assert not [k for k in KNOWLEDGE_KEYS if k not in KnowledgeModule.default_config]
    assert not [k for k in KNOWLEDGE_KEYS if k not in KnowledgeModule.config_schema]


def test_modules_are_pure_business():
    """v3：记忆与知识库是**业务**，不声明 supersedes（不是接管）。"""
    assert not getattr(MemoryModule, "supersedes", ())
    assert not getattr(KnowledgeModule, "supersedes", ())
    assert MemoryModule.category == KnowledgeModule.category == "LLM"


# ---------- 框架读取点跟随模块 ----------


def test_memory_manager_reads_module_config(tmp_path, monkeypatch):
    from app.plugins.llm_memory.src.manager import MemoryManager

    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    runtime = SimpleNamespace(
        bot_id="100",
        config={"memory_enable": True},
        memory_options=SimpleNamespace(get=lambda k, d=None: {"memory_enable": False}.get(k, d)),
    )
    mgr = MemoryManager(runtime)
    assert mgr.enabled() is False, "模块关掉时框架必须跟着关"


def test_memory_manager_falls_back_without_module(tmp_path, monkeypatch):
    from app.plugins.llm_memory.src.manager import MemoryManager

    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    runtime = SimpleNamespace(bot_id="101", config={"memory_enable": True})
    assert MemoryManager(runtime).enabled() is True


def test_knowledge_manager_reads_module_config(tmp_path, monkeypatch):
    from app.plugins.llm_knowledge.src.manager import KnowledgeManager

    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    runtime = SimpleNamespace(
        bot_id="200",
        config={"knowledge_enable": False},
        knowledge_options=SimpleNamespace(
            get=lambda k, d=None: {"knowledge_enable": True}.get(k, d)
        ),
    )
    assert KnowledgeManager(runtime).enabled() is True


def test_knowledge_manager_falls_back_without_module(tmp_path, monkeypatch):
    from app.plugins.llm_knowledge.src.manager import KnowledgeManager

    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    runtime = SimpleNamespace(bot_id="201", config={"knowledge_enable": False})
    assert KnowledgeManager(runtime).enabled() is False


# ---------- 装/卸 ----------


class _Hooks:
    def __init__(self):
        self.registered: list[dict] = []

    def register(self, **kwargs):
        self.registered.append(kwargs)


class _Runtime:
    def __init__(self, **over):
        self.bot_id = "12345"
        self.config = {}
        self.memory_options = None
        self.knowledge_options = None
        self.context_providers: list = []
        self.tool_sources: list = []
        self.llm_hooks = _Hooks()
        for key, value in over.items():
            setattr(self, key, value)


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


def _make(module_cls, name, runtime, values=None):
    from app.plugin.base import ModuleAuthority, ModuleConfig, ModuleContext, ServiceAccess

    service = _CfgService(values or {})
    ctx = ModuleContext(
        module_name=name,
        bot_id="12345",
        config=ModuleConfig(name, "12345", module_cls.default_config, service),
        authority=ModuleAuthority(name, "12345", service),
        services=ServiceAccess(runtime=lambda bot_id: runtime),
    )
    return type("M", (module_cls,), {})(ctx)


async def test_memory_module_installs_and_clears_view():
    runtime = _Runtime()
    runtime.config = {"memory_enable": True}
    module = _make(MemoryModule, "llm_memory", runtime, {"memory_enable": False})

    await module.on_load()
    assert runtime.memory_options is not None
    assert policy_get(runtime, "memory", "memory_enable") is False
    assert module.enabled is False
    # 三个接入点都挂上了（上下文块 / 工具源 / 两个钩子）
    assert [p.name for p in runtime.context_providers] == ["memory"]
    assert [s.name for s in runtime.tool_sources] == ["memory_tools"]
    stages = {(h["stage"], h["order"]) for h in runtime.llm_hooks.registered}
    assert ("pre_request", 60) in stages and ("turn_completed", 60) in stages

    await module.on_unload()
    assert runtime.memory_options is None
    assert policy_get(runtime, "memory", "memory_enable") is True


async def test_knowledge_module_installs_and_clears_view():
    runtime = _Runtime()
    runtime.config = {"knowledge_enable": False}
    module = _make(KnowledgeModule, "llm_knowledge", runtime, {"knowledge_enable": True})

    await module.on_load()
    assert policy_get(runtime, "knowledge", "knowledge_enable") is True
    assert module.enabled is True

    await module.on_unload()
    assert runtime.knowledge_options is None


@pytest.mark.parametrize(
    "key", ["memory_recall_max", "memory_min_confidence", "memory_on_reset"]
)
async def test_memory_keys_overridable(key):
    runtime = _Runtime()
    runtime.config = {key: "agent"}
    module = _make(MemoryModule, "llm_memory", runtime, {key: "module"})
    await module.on_load()
    assert policy_get(runtime, "memory", key) == "module"
    await module.on_unload()


async def test_modules_tolerate_missing_runtime():
    for cls, name in ((MemoryModule, "llm_memory"), (KnowledgeModule, "llm_knowledge")):
        from app.plugin.base import ModuleAuthority, ModuleConfig, ModuleContext, ServiceAccess

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
