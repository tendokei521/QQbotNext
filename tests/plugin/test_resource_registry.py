"""系统资源注册表（`ResourceRegistry`）与候选 API。

模块配置里想「直接选系统的角色预设 / Provider 模型」时，原先只能手抄 ID；
这里给框架资源一个统一目录与候选接口，前端 `type: "resource"` 字段据此渲染下拉框。
"""

from __future__ import annotations

import pytest

from app.application.resource_registry import ResourceQuery, ResourceRegistry


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


async def test_role_preset_options_come_from_the_real_service(container):
    """真实链路：注册表 → RolePresetService → 候选列表。"""
    from app.application.role_preset_service import RolePresetService

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
    from app.application.provider_model_service import ProviderModelService
    from app.application.provider_preset_service import ProviderPresetService

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

    from app.application.bot_service import BotService
    from app.plugin.registry import ModuleRegistry

    # BotService 持有的是容器里的注册表实例：把它的模块目录定向到仓库真实目录，
    # 这样加载出来的模块才会经过 bot_service 的 schema 归一化链路。
    registry = container.get(ModuleRegistry)
    registry.plugins_dir = Path(__file__).resolve().parents[2] / "app" / "plugins"
    bot_id = 20250930
    assert await registry.load_single("llm_knowledge", bot_id) is True
    try:
        data = container.get(BotService).get_modules_data(bot_id)
        field = data["llm_knowledge"]["config_schema"]["items"]["knowledge_embedding_model_id"]
        assert field["type"] == "resource"
        assert field["resource"] == "provider_model"
        assert field["issues"] == [], "种类必须已注册，否则界面会显示「资源种类未注册」"
        assert field["source_module"] == "llm_knowledge"
    finally:
        await registry.unload_single("llm_knowledge", bot_id)


# 旧版 UI（/legacy）与 static/js/widgets.js 的两个用例已随 P6 删除：
# 前端只剩 app/webui/dashboard/dist 一套，资源选择器改由 Vue 的 ResourcePicker 组件实现，
# 对应前端测试在 app/webui/dashboard/ 内的构建期检查。


# ==================== 资源变更 → WS 广播（前端据此局部刷新下拉） ====================


async def test_resources_updated_broadcast_only_for_resource_scopes(container, monkeypatch):
    """配置变更按 scope 映射成资源种类广播；非资源 scope 不该产生该事件。"""
    from app.infrastructure.config.config_service import ConfigService
    from app.webui import app as webui_app
    from app.webui.ws import manager

    sent: list[str] = []

    async def fake_broadcast(payload):
        sent.append(payload)

    monkeypatch.setattr(manager, "broadcast", fake_broadcast)

    captured: dict = {}
    config_service = container.get(ConfigService)
    monkeypatch.setattr(config_service, "on_change", lambda cb: captured.setdefault("cb", cb))
    webui_app._install_config_listener(container)
    on_change = captured["cb"]

    def resource_events():
        return [p for p in sent if '"resources_updated"' in p]

    await on_change("role_presets", [])
    assert any("role_preset" in p for p in resource_events()), "角色预设变更要触达资源选择器"

    sent.clear()
    await on_change("provider_models", [])
    assert any("provider_model" in p for p in resource_events())

    sent.clear()
    assert resource_events() == []

    sent.clear()
    await on_change("webui", {})  # 非资源 scope
    assert resource_events() == []


# ==================== bot / group 种类（按账号） ====================


async def test_bot_kind_uses_login_snapshot(container, monkeypatch):
    """bot 候选来自登录账号快照（早期版本读了连接配置里不存在的 bot_id，永远为空）。"""
    from app.infrastructure.config.config_service import ConfigService

    config_service = container.get(ConfigService)
    monkeypatch.setattr(
        config_service,
        "get_bot_accounts",
        lambda: {0: {"user_id": "10001", "nickname": "甲"}, 1: {"user_id": "", "nickname": "未登录"}},
    )
    options = await container.get(ResourceRegistry).options("bot")
    assert options == [{"value": "10001", "label": "#0 甲"}], "空账号的连接要被跳过"


async def test_group_kind_needs_bot_and_reads_group_list(container, monkeypatch):
    """group 候选按账号取；账号不在线时返回空（不是报错）。"""
    from app.infrastructure.config.config_service import ConfigService
    from app.infrastructure.onebot.gateway import OneBotGateway

    config_service = container.get(ConfigService)
    monkeypatch.setattr(config_service, "get_bot_accounts", lambda: {0: {"user_id": "10001", "nickname": "甲"}})
    registry = container.get(ResourceRegistry)

    assert await registry.options("group") == [], "没给 bot_id → 空候选"
    assert await registry.options("group", bot_id="99999") == [], "账号不在快照里 → 空候选"

    class _FakeBot:
        async def get_group_list(self):
            return {"data": [{"group_id": 777, "group_name": "测试群"}, {"group_id": 888, "group_name": ""}]}

    container.get(OneBotGateway).connections[0] = _FakeBot()
    options = await registry.options("group", bot_id="10001")
    assert options == [
        {"value": "777", "label": "测试群"},
        {"value": "888", "label": "888"},
    ], "群名为空时回退 group_id，绝不产出空标签"

    meta = registry.get("group")
    assert meta is not None and meta.needs_bot is True, "该种类必须声明 needs_bot"
