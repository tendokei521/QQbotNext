"""回归测试：agent 配置保存不能被"回声的模块键"拦死。

历史 bug：GET 回传全部键（含 120 个模块拥有的旧键），前端整份 POST 回来，
写入校验一律 400 → 界面任何保存都失败（截图里那条长红字）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent.config import module_owned_keys
from app.webui.api import agent as agent_api
from app.webui.api.deps import get_config_service, get_agent_manager


class _Config:
    """配置对象（真实运行时是 `AgentConfig`）。"""

    def __init__(self, stored: dict) -> None:
        self._stored = stored
        self.saved = 0
        self.enabled = True
        self.permission = SimpleNamespace(
            group_mode="blacklist", group_list=[], user_mode="blacklist", user_list=[]
        )

    @property
    def raw_config(self) -> dict:
        return dict(self._stored)

    def get(self, key, default=None):
        return self._stored.get(key, default)

    def set(self, key, value, auto_save=False):
        self._stored[key] = value

    def save(self):
        self.saved += 1

    def set_enabled(self, value):
        self.enabled = bool(value)

    def update_permission(self, *a, **k):
        return None


class _Runtime:
    """最小运行时：带一份"含模块旧键"的配置。"""

    def __init__(self) -> None:
        self.config = _Config({
            "group_enable": True,
            "private_enable": True,
            # 模块拥有的旧键（迁移残渣，值一直没变）
            "assembly_profile": "qq_agent",
            "speech_via_tool": False,
            "onebot_tools_enable": True,
        })
        self.bot_id = "3437542570"


class _AgentManager:
    def __init__(self, runtime):
        self.runtime = runtime

    def get_runtime(self, bot_id):
        return self.runtime


class _ConfigService:
    def get_module_config(self, name, bot_id):
        return {}

    def get_webui_config(self):
        return {}


class _RolePresetService:
    async def ensure_legacy_prompt(self, bot_id, prompt):
        return None

    def list_presets(self):
        return []


class _ResourceRegistry:
    def list_all(self):
        return []


class _ProviderPresetService:
    def list_presets(self):
        return []


class _ProviderModelService:
    def list_models(self):
        return []


@pytest.fixture
def client(monkeypatch):
    runtime = _Runtime()
    app = FastAPI()
    app.include_router(agent_api.router, prefix="/api")
    overrides = {
        get_agent_manager: lambda: _AgentManager(runtime),
        get_config_service: lambda: _ConfigService(),
        agent_api.get_role_preset_service: lambda: _RolePresetService(),
        agent_api.get_resource_registry: lambda: _ResourceRegistry(),
        agent_api.get_provider_preset_service: lambda: _ProviderPresetService(),
        agent_api.get_provider_model_service: lambda: _ProviderModelService(),
    }
    for dep, provider in overrides.items():
        app.dependency_overrides[dep] = provider
    return TestClient(app), runtime


def test_get_config_hides_module_owned_keys(client):
    """模块拥有的键不该出现在 agent 配置视图里。"""
    http, _runtime = client
    resp = http.get("/api/agent/config", params={"bot_id": 3437542570})
    assert resp.status_code == 200, resp.text
    config = resp.json()["config"]
    for key in ("assembly_profile", "speech_via_tool", "onebot_tools_enable"):
        assert key not in config, f"{key} 是模块键，不该回传给 agent 页"


def test_small_save_is_not_blocked_by_echoed_module_keys(client):
    """前端把整份配置原样提交（含模块旧键、值没变）必须能保存。"""
    http, runtime = client
    body = {
        "config": {
            "group_enable": True,
            "private_enable": True,
            # 回声：值未变
            "assembly_profile": "qq_agent",
            "onebot_tools_enable": True,
        },
    }
    resp = http.post("/api/agent/config", params={"bot_id": 3437542570}, json=body)
    assert resp.status_code == 200, resp.text
    assert runtime.config.saved >= 1


def test_changing_a_module_key_is_still_rejected(client):
    """真的想改模块键 → 仍然 400，并点出是哪个键。"""
    http, runtime = client
    body = {"config": {"onebot_tools_enable": False}}   # 值变了
    resp = http.post("/api/agent/config", params={"bot_id": 3437542570}, json=body)
    assert resp.status_code == 400
    assert "onebot_tools_enable" in resp.json()["message"]
    assert runtime.config.get("onebot_tools_enable") is True, "被拒的写入不能生效"


def test_module_owned_keys_cover_migrated_fields():
    """迁移清单里的键必须都被认定成模块拥有（否则仍会写错抽屉）。"""
    owned = module_owned_keys()
    for key in ("assembly_profile", "speech_via_tool", "onebot_tools_enable", "memory_enable"):
        assert key in owned, f"{key} 应归模块"
    assert "group_enable" not in owned, "本体键不该被当成模块键"


# ==================== 坏请求体：不许"假成功"，也不许 500 ====================


def _post_raw(http, body: bytes):
    return http.post(
        "/api/agent/config",
        params={"bot_id": 3437542570},
        content=body,
        headers={"Content-Type": "application/json"},
    )


def test_malformed_json_is_rejected_instead_of_silently_ok(client):
    """坏 JSON 曾经折成 {} → 回 200；现在必须 400 且不动配置。"""
    http, runtime = client
    resp = _post_raw(http, b"{not json")
    assert resp.status_code == 400
    assert "JSON" in resp.json()["message"]
    assert runtime.config.saved == 0


def test_non_object_body_is_rejected_instead_of_500(client):
    """顶层是数组时，旧实现 data.get(...) 抛 AttributeError → 500。"""
    http, runtime = client
    resp = _post_raw(http, b"[1, 2, 3]")
    assert resp.status_code == 400
    assert runtime.config.saved == 0


def test_config_must_be_object(client):
    http, runtime = client
    resp = http.post(
        "/api/agent/config", params={"bot_id": 3437542570}, json={"config": "nope"}
    )
    assert resp.status_code == 400
    assert runtime.config.saved == 0


def test_permission_must_be_object(client):
    http, runtime = client
    resp = http.post(
        "/api/agent/config",
        params={"bot_id": 3437542570},
        json={"permission": ["blacklist"]},
    )
    assert resp.status_code == 400
    assert runtime.config.saved == 0
