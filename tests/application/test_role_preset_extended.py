"""角色预设扩展字段 + 服务进插件 API。

迁移到框架的场景需要「可复用的人设」，而 AstrBot 的 persona 不只是 system
prompt——还带 ``begin_dialogs``（预设对话）/ ``tools`` / ``skills``。这里锁住：

1. 三个扩展字段的存储、校验与归一化（脏数据不落库）；
2. 旧预设（库里没有这些键）读出来形状一致（begin_dialogs=[] / tools=None / skills=None）；
3. 更新时省略扩展字段不丢原值；
4. ``ServiceAccess`` 暴露 RolePresetService / ConfigProfileService，插件不必再伸手取容器。
"""

from __future__ import annotations

import pytest

from app.plugin.base import ServiceAccess
from app.application.config_profile_service import ConfigProfileService
from app.application.role_preset_service import RolePresetService


@pytest.fixture
def role_service(config_service):
    return RolePresetService(config_service)


@pytest.fixture
def profile_service(config_service):
    return ConfigProfileService(config_service)


# ==================== 扩展字段 ====================


async def test_create_with_extended_fields(role_service):
    preset = await role_service.create_preset(
        "傲娇猫娘",
        "示例人设",
        "你是傲娇猫娘。",
        begin_dialogs=[
            {"role": "user", "content": "在吗"},
            {"role": "assistant", "content": "哼，在的。"},
        ],
        tools=["query_weather"],
        skills=["周报助手"],
    )
    assert preset["begin_dialogs"] == [
        {"role": "user", "content": "在吗"},
        {"role": "assistant", "content": "哼，在的。"},
    ]
    assert preset["tools"] == ["query_weather"]
    assert preset["skills"] == ["周报助手"]

    # 落库并读回
    loaded = role_service.get_preset(preset["id"])
    assert loaded is not None
    assert loaded["begin_dialogs"] == preset["begin_dialogs"]
    assert loaded["tools"] == ["query_weather"]


async def test_begin_dialogs_normalized(role_service):
    preset = await role_service.create_preset(
        "n",
        "",
        "p",
        begin_dialogs=[
            {"role": "USER", "content": "  你好  "},
            {"role": "system", "content": "角色不对，应丢弃"},
            {"role": "user", "content": "   "},
            {"role": "assistant", "content": "在"},
            "不是字典",
            {"content": "缺 role"},
        ],
    )
    assert preset["begin_dialogs"] == [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "在"},
    ]


def test_name_list_semantics():
    """None=不限制；[]=显式禁用；字符串按单元素；去重保序。"""
    from app.application.role_preset_service import normalize_name_list

    assert normalize_name_list(None) is None
    assert normalize_name_list([]) == []
    assert normalize_name_list("a") == ["a"]
    assert normalize_name_list(["a", "a", "b", "  ", None]) == ["a", "b"]
    assert normalize_name_list({"a": 1}) is None  # 非列表/字符串 → 视为不限制


# ==================== 向后兼容 ====================


async def test_legacy_preset_gets_defaults(config_service, role_service):
    """库里已有的旧预设（无扩展键）读出来要有统一形状。"""
    await config_service.save_role_preset("legacy1", {
        "id": "legacy1",
        "name": "旧角色",
        "description": "",
        "system_prompt": "旧提示词",
    })

    loaded = role_service.get_preset("legacy1")
    assert loaded["system_prompt"] == "旧提示词"
    assert loaded["begin_dialogs"] == []
    assert loaded["tools"] is None
    assert loaded["skills"] is None

    listed = [p for p in role_service.list_presets() if p["id"] == "legacy1"][0]
    assert listed["begin_dialogs"] == [] and listed["tools"] is None


async def test_update_without_extended_fields_keeps_old_values(role_service):
    preset = await role_service.create_preset(
        "n", "", "p", begin_dialogs=[{"role": "user", "content": "hi"}], tools=["t1"], skills=["s1"]
    )
    updated = await role_service.update_preset(preset["id"], {"name": "n2", "system_prompt": "p2"})
    assert updated["name"] == "n2"
    assert updated["begin_dialogs"] == [{"role": "user", "content": "hi"}]
    assert updated["tools"] == ["t1"]
    assert updated["skills"] == ["s1"]


async def test_update_can_clear_extended_fields(role_service):
    preset = await role_service.create_preset("n", "", "p", begin_dialogs=[{"role": "user", "content": "hi"}], tools=["t1"])
    updated = await role_service.update_preset(preset["id"], {"begin_dialogs": [], "tools": []})
    assert updated["begin_dialogs"] == []
    assert updated["tools"] == []


# ==================== 插件 API 暴露 ====================


def test_service_access_exposes_preset_and_profile_services(container):
    """插件通过 ctx.services 就能拿到，不必再从 bootstrap 取容器。"""
    services = container.get(ServiceAccess)
    assert isinstance(services.role_presets, RolePresetService)
    assert isinstance(services.config_profiles, ConfigProfileService)
    assert services.role_presets is container.get(RolePresetService)
    assert services.config_profiles is container.get(ConfigProfileService)


async def test_plugin_can_build_persona_through_service_access(container):
    """端到端：插件侧拿 services.role_presets 建人设 + 用档案路由绑定会话。"""
    services = container.get(ServiceAccess)
    preset = await services.role_presets.create_preset(
        "迁移人设", "", "你是迁移过来的人设。", begin_dialogs=[{"role": "user", "content": "你好"}]
    )
    profile = await services.config_profiles.create_profile("迁移档案", {"temperature": 0.9})
    await services.config_profiles.set_route("group_123", profile["id"])

    assert services.role_presets.get_preset(preset["id"])["begin_dialogs"] == [{"role": "user", "content": "你好"}]
    assert services.config_profiles.get_route("group_123") == profile["id"]
