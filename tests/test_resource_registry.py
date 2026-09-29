"""系统资源注册表（`ResourceRegistry`）与候选 API。

模块配置里想「直接选系统的角色预设 / Provider 模型」时，原先只能手抄 ID；
这里给框架资源一个统一目录与候选接口，前端 `type: "resource"` 字段据此渲染下拉框。
"""

from __future__ import annotations

import pytest

from app.services.resource_registry import ResourceQuery, ResourceRegistry


def _registry():
    reg = ResourceRegistry()
    reg.register(
        "role_preset",
        lambda q: [
            {"id": "p1", "name": "傲娇猫娘"},
            {"id": "p2", "name": "冷静助手"},
        ],
        label="角色预设",
    )
    return reg


# ==================== 注册表本身 ====================


async def test_options_normalizes_to_value_label():
    reg = _registry()
    options = await reg.options("role_preset")
    assert options == [
        {"value": "p1", "label": "傲娇猫娘"},
        {"value": "p2", "label": "冷静助手"},
    ]


async def test_custom_value_and_label_fields():
    reg = ResourceRegistry()
    reg.register(
        "thing",
        lambda q: [{"code": 7, "title": "第七件", "junk": "x"}],
        label="东西", value_field="code", label_field="title",
    )
    assert await reg.options("thing") == [{"value": "7", "label": "第七件"}]


async def test_label_falls_back_to_id_and_name_chain():
    """资源对象没给 label_field 时回退 name/title/value，绝不显示空标签。"""
    reg = ResourceRegistry()
    reg.register("a", lambda q: [{"id": "x"}], label="A")
    reg.register("b", lambda q: [{"id": "y", "title": "标题"}], label="B")
    reg.register("c", lambda q: ["raw"], label="C")
    assert await reg.options("a") == [{"value": "x", "label": "x"}]
    assert await reg.options("b") == [{"value": "y", "label": "标题"}]
    assert await reg.options("c") == [{"value": "raw", "label": "raw"}]


async def test_handler_may_be_async_and_return_wrapped_dict():
    reg = ResourceRegistry()

    async def handler(q: ResourceQuery):
        return {"items": [{"id": "1", "name": "一"}]}

    reg.register("wrapped", handler, label="包装过的")
    assert await reg.options("wrapped") == [{"value": "1", "label": "一"}]


async def test_filters_and_bot_id_reach_handler():
    reg = ResourceRegistry()
    seen = {}

    def handler(q: ResourceQuery):
        seen["bot_id"] = q.bot_id
        seen["filters"] = dict(q.filters)
        return []

    reg.register("ctx", handler, label="上下文")
    await reg.options("ctx", bot_id="42", filters={"provider_type": "embedding"})
    assert seen == {"bot_id": "42", "filters": {"provider_type": "embedding"}}


async def test_unknown_kind_is_empty_and_does_not_raise():
    reg = _registry()
    assert await reg.options("never_registered") == []
    assert await reg.call("never_registered") == []
    assert reg.has("never_registered") is False


async def test_handler_exception_is_swallowed():
    """候选拉取失败不该让表单/请求炸掉：返回空并留痕。"""
    reg = ResourceRegistry()

    def boom(q):
        raise RuntimeError("炸了")

    reg.register("boom", boom, label="会炸的")
    assert await reg.options("boom") == []


async def test_register_overrides_and_unregister():
    reg = ResourceRegistry()
    reg.register("k", lambda q: [{"id": "old"}], label="旧")
    reg.register("k", lambda q: [{"id": "new"}], label="新")
    assert await reg.options("k") == [{"value": "new", "label": "new"}]
    reg.unregister("k")
    assert reg.has("k") is False


def test_kinds_are_sorted_and_carry_metadata():
    reg = ResourceRegistry()
    reg.register("zeta", lambda q: [], label="Z", desc="z desc", needs_bot=True)
    reg.register("alpha", lambda q: [], label="A")
    kinds = reg.kinds()
    assert [k["kind"] for k in kinds] == ["alpha", "zeta"]
    assert kinds[1] == {"kind": "zeta", "label": "Z", "desc": "z desc", "needs_bot": True}


def test_register_requires_kind():
    reg = ResourceRegistry()
    with pytest.raises(ValueError):
        reg.register("  ", lambda q: [], label="空")


# ==================== 容器内置资源与 API ====================


def test_builtin_kinds_are_registered_in_container(container):
    """框架自带资源种类：不依赖任何模块就应可用。"""
    reg = container.get(ResourceRegistry)
    kinds = {k["kind"] for k in reg.kinds()}
    assert {"role_preset", "config_profile", "provider_preset", "provider_model", "bot", "module"} <= kinds


async def test_role_preset_options_come_from_the_real_service(container):
    """真实链路：注册表 → RolePresetService → 候选列表。"""
    from app.services.role_preset_service import RolePresetService

    await container.get(RolePresetService).create_preset("迁移人设", "", "你是迁移过来的人设。")
    options = await container.get(ResourceRegistry).options("role_preset")
    assert any(o["label"] == "迁移人设" for o in options)
    assert all(o["value"] and o["label"] for o in options)


async def test_module_resource_lists_loaded_modules(container):
    """模块候选来自 ModuleRegistry（未加载任何模块时为空，但不炸）。"""
    reg = container.get(ResourceRegistry)
    assert await reg.options("module") == []


async def test_provider_model_filter_by_type(container):
    """provider_model 支持 provider_type 过滤（embedding 场景用它）。"""
    from app.services.provider_model_service import ProviderModelService
    from app.services.provider_preset_service import ProviderPresetService

    preset = await container.get(ProviderPresetService).create_preset(
        {"name": "测试预设", "provider": "openai", "config": {"api_base": "http://x", "api_key": "sk-test"}}
    )
    models = container.get(ProviderModelService)
    await models.create_model(preset["id"], {"model": "chat-model", "provider_type": "chat"})
    await models.create_model(preset["id"], {"model": "embed-model", "provider_type": "embedding"})

    reg = container.get(ResourceRegistry)
    all_rows = await reg.options("provider_model")
    emb_rows = await reg.options("provider_model", filters={"provider_type": "embedding"})
    assert len(all_rows) == 2
    assert len(emb_rows) == 1 and "embed-model" in emb_rows[0]["label"]
    assert "（embedding）" in emb_rows[0]["label"], "label 带上能力类型便于区分"


# ==================== WebUI 端点 ====================


def test_resources_endpoint(container):
    from starlette.testclient import TestClient

    from app.webui.app import create_app

    client = TestClient(create_app(container))
    resp = client.get("/api/resources")
    assert resp.status_code == 200
    kinds = {k["kind"] for k in resp.json()["kinds"]}
    assert "role_preset" in kinds

    resp = client.get("/api/resources/role_preset")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True and "options" in body


def test_resources_endpoint_unknown_kind_returns_404(container):
    """未注册的种类要 404（前端据此提示"资源种类未注册"），而不是空数组掩盖问题。"""
    from starlette.testclient import TestClient

    from app.webui.app import create_app

    client = TestClient(create_app(container))
    resp = client.get("/api/resources/never_registered")
    assert resp.status_code == 404
    assert "未注册" in resp.json()["message"]


def test_resources_endpoint_filters_pass_through(container):
    from starlette.testclient import TestClient

    from app.webui.app import create_app

    client = TestClient(create_app(container))
    resp = client.get("/api/resources/provider_model?provider_type=embedding&bot_id=1")
    assert resp.status_code == 200
    assert resp.json()["kind"] == "provider_model"


async def test_module_schema_reaches_api_with_resource_field(container):
    """端到端（API 合同层）：WebUI 拿到的模块 schema 里，人设字段是可渲染的资源引用。

    这是"界面能直接选系统预设"的最小证据链：模块注册表加载 → schema 归一化
    （种类校验 + issues + source_module）→ bot_service 下发 → 前端按 resource 拉候选。
    """
    from pathlib import Path

    from app.modules.registry import ModuleRegistry
    from app.services.bot_service import BotService

    # BotService 持有的是容器里的注册表实例：把它的模块目录定向到仓库真实目录，
    # 这样加载出来的模块才会经过 bot_service 的 schema 归一化链路。
    registry = container.get(ModuleRegistry)
    registry.modules_dir = Path(__file__).resolve().parent.parent / "module" / "modules"
    bot_id = 20250930
    assert await registry.load_single("astrbot_alignment", bot_id) is True
    try:
        data = container.get(BotService).get_modules_data(bot_id)
        field = data["astrbot_alignment"]["config_schema"]["items"]["persona_preset_id"]
        assert field["type"] == "resource"
        assert field["resource"] == "role_preset"
        assert field["issues"] == [], "种类必须已注册，否则界面会显示「资源种类未注册」"
        assert field["source_module"] == "astrbot_alignment"
    finally:
        await registry.unload_single("astrbot_alignment", bot_id)


async def test_legacy_ui_renders_resource_widget(container):
    """旧版 UI（/legacy）也要能渲染资源选择器，否则回退路径下资源字段会退化成文本框。"""
    from pathlib import Path

    from starlette.testclient import TestClient

    from app.modules.registry import ModuleRegistry
    from app.webui.app import create_app

    registry = container.get(ModuleRegistry)
    registry.modules_dir = Path(__file__).resolve().parent.parent / "module" / "modules"
    assert await registry.load_single("astrbot_alignment", None) is True
    try:
        html = TestClient(create_app(container)).get("/legacy").text
        assert 'data-widget-type="resource"' in html, "模板应输出资源组件占位"
        assert 'data-resource="role_preset"' in html
        assert 'data-multiple="0"' in html
        assert "persona_preset_id" in html
    finally:
        await registry.unload_single("astrbot_alignment", None)


def test_legacy_widget_script_implements_resource_picker():
    """静态自检：旧版组件脚本确实实现了资源选择器（且带可读诊断）。"""
    from pathlib import Path

    js = (
        Path(__file__).resolve().parent.parent / "app" / "webui" / "static" / "js" / "widgets.js"
    ).read_text(encoding="utf-8")
    assert "function initResourceWidget" in js
    assert "encodeURIComponent(kind)" in js, "必须按种类拉候选"
    assert "资源种类未注册" in js and "（已删除或未找到）" in js, "未注册/悬挂引用要有可读提示"
    assert "initResourceWidget(moduleName, key, el)" in js, "必须在 initConfigWidgets 里注册"
