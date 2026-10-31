"""框架能力清单 + 模块依赖自检（P1）。

锁住三件事：

1. `collect_capabilities()`：清单形状稳定（版本/schema 版本/字段类型/资源种类/服务/能力），
   且任一子项取不到时降级为空而不是抛异常；
2. `BaseModule.requires`：加载时自检 —— 缺失只 WARNING 不阻断，模块可据此降级；
3. `GET /api/framework/capabilities`：同一份清单的 HTTP 出口。
"""

from __future__ import annotations

from app.application.capabilities import collect_capabilities
from app.plugin.base import BaseModule, ModuleAuthority, ModuleConfig, ModuleContext, ServiceAccess
from app.plugin.features import FeatureRegistry
from app.plugin.requirements import declared_requirements, missing_requirements


class _Svc:
    def get_module_config(self, module, bot_id):
        return {}

    def set_module_config(self, module, bot_id, data, persist=True):
        return None


class _AuthSvc:
    def get_module_authority(self, module, bot_id):
        return {}

    def set_module_authority(self, module, bot_id, authority):
        return None


def _module(services, requires=()):
    config = ModuleConfig("t", "bot_cap", {}, _Svc())
    authority = ModuleAuthority("t", "bot_cap", _AuthSvc())
    ctx = ModuleContext(module_name="t", bot_id="bot_cap", config=config, authority=authority, services=services)
    cls = type("_Mod", (BaseModule,), {"requires": tuple(requires), "name": "清单测试", "sign": "T"})
    return cls(ctx)


# ==================== collect_capabilities ====================


def test_collect_capabilities_shape_without_anything():
    """什么都不给：形状仍然齐全，且不抛异常（诊断接口不能自己成为故障点）。"""
    caps = collect_capabilities()
    assert set(caps) == {
        "framework_version", "schema_version", "schema_types", "resource_types",
        "resources", "services", "features",
    }
    assert caps["resources"] == [] and caps["services"] == [] and caps["features"] == []
    assert isinstance(caps["framework_version"], str) and caps["framework_version"]
    assert caps["schema_version"] >= 2


def test_collect_capabilities_tolerates_broken_sources():
    class _Boom:
        def list_features(self):
            raise RuntimeError("炸了")

        def kinds(self):
            raise RuntimeError("炸了")

    caps = collect_capabilities(features=_Boom(), resources=_Boom())
    assert caps["features"] == [] and caps["resources"] == []


def test_capabilities_with_real_container(container):
    caps = container.get(ServiceAccess).capabilities()
    assert "llm" in caps["services"], "模块 LLM 客户端应出现在清单里"
    assert "resources" in caps["services"]
    assert "data_sources" in caps["services"]
    kinds = {k["kind"] for k in caps["resources"]}
    assert {"role_preset", "provider_model"} <= kinds
    assert {"assembly", "provider_invoker"} <= set(caps["features"])
    assert "resource" in caps["schema_types"] and "resources" in caps["schema_types"]


def test_capabilities_endpoint(container):
    from starlette.testclient import TestClient

    from app.webui.app import create_app

    resp = TestClient(create_app(container)).get("/api/framework/capabilities")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "llm" in body["capabilities"]["services"]
    assert body["capabilities"]["schema_version"] >= 2


# ==================== requires 自检 ====================


def test_declared_requirements_dedupes_and_strips():
    module = _module(ServiceAccess(), requires=("llm", " llm ", "", None, "role_presets"))
    assert declared_requirements(module) == ["llm", "role_presets"]


def test_missing_requirements_empty_when_not_declared():
    assert missing_requirements(_module(ServiceAccess())) == []


def test_missing_requirements_satisfied_by_service():
    assert missing_requirements(_module(ServiceAccess(llm=object()), requires=("llm",))) == []
    assert missing_requirements(_module(ServiceAccess(), requires=("llm",))) == ["llm"]


def test_missing_requirements_satisfied_by_feature():
    """能力既可声明服务（ctx.services.x），也可声明 feature_id（可被接管的框架能力）。"""
    from app.plugin.features import AssemblerFeatureController

    class _Manager:
        def get_runtime(self, bot_id):
            return None

    features = FeatureRegistry()
    features.register(AssemblerFeatureController(_Manager()))
    services = ServiceAccess(features=features)

    assert missing_requirements(_module(services, requires=("assembly",))) == []
    assert missing_requirements(_module(services, requires=("nonexistent",))) == ["nonexistent"]


def test_missing_requirements_uses_ctx_services_when_not_passed():
    module = _module(ServiceAccess(llm=object()), requires=("llm", "provider_models"))
    assert module.missing_requirements() == ["provider_models"]


async def test_registry_checks_requirements_on_load(container, monkeypatch):
    """加载模块时会跑一次自检（缺失只警告，不阻断加载）。"""
    from pathlib import Path

    from app.plugin.registry import ModuleRegistry

    calls: list[tuple[str, list[str]]] = []
    original = BaseModule.missing_requirements

    def spy(self):
        result = original(self)
        calls.append((self.module_name, result))
        return result

    monkeypatch.setattr(BaseModule, "missing_requirements", spy)

    registry = container.get(ModuleRegistry)
    registry.plugins_dir = Path(__file__).resolve().parents[2] / "app" / "plugins"
    try:
        assert await registry.load_single("llm_knowledge", None) is True
        assert any(name == "llm_knowledge" for name, _missing in calls), "加载时应做过依赖自检"
    finally:
        await registry.unload_single("llm_knowledge", None)
