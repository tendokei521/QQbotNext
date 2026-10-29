"""`llm_memory`（17 键）与 `llm_knowledge`（3 键）模块测试。

验证"策略搬走"：把模块值设成与 agent 相反，`MemoryManager.enabled()` /
`KnowledgeManager.enabled()` 必须跟着模块走；卸载后回退。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.agent.policy import policy_get
from app.plugins.llm_knowledge.main import Module as KnowledgeModule
from app.plugins.llm_memory.main import Module as MemoryModule

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
