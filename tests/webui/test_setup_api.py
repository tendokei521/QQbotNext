"""P1-5：向导「一次建好模型并入池」。

现状痛点：建预设 → 建模型 → 回 Agent 页「添加模型」是三次跳页，
漏掉最后一步就表现为"模型建了但 Agent 不回话"。这里锁定一次调用完成三件事，
并保证幂等（重复点「下一步」不会堆出一堆预设/模型）。
"""

from __future__ import annotations

from starlette.testclient import TestClient

from app.agent.runtime import AgentManager
from app.application.provider_model_service import ProviderModelService
from app.application.provider_preset_service import ProviderPresetService
from app.application.setup_service import SetupError, SetupService
from app.infrastructure.config.config_service import ConfigService
from app.webui.app import create_app


def _service(container) -> SetupService:
    return SetupService(
        container.get(ConfigService),
        container.get(AgentManager),
        container.get(ProviderPresetService),
        container.get(ProviderModelService),
    )


async def _seed(container, config_service, account_id="acc1"):
    await config_service.save_bots([
        {"id": account_id, "name": "主号", "ws_url": "ws://127.0.0.1:3001", "auto_connect": False},
    ])


async def test_quick_model_creates_preset_model_and_pool(container, config_service):
    await _seed(container, config_service)
    service = _service(container)

    result = await service.quick_model(
        "acc1",
        provider="openai",
        api_base="https://api.deepseek.com/v1",
        api_key="sk-test",
        model="deepseek-chat",
    )

    assert result["preset_id"] and result["model_id"]
    assert result["model"] == "deepseek-chat"
    assert result["model_id"] in result["pool"]

    presets = config_service.list_provider_presets()
    assert any(p["id"] == result["preset_id"] for p in presets)
    models = config_service.list_provider_models(result["preset_id"])
    assert any(m["id"] == result["model_id"] and m["model"] == "deepseek-chat" for m in models)

    runtime = container.get(AgentManager).get_runtime("acc1")
    assert result["model_id"] in runtime.config.get("provider_model_pool")
    assert runtime.config.get("provider_model_id") == result["model_id"]
    assert runtime.config.get("provider_preset_id") == result["preset_id"]


async def test_quick_model_is_idempotent(container, config_service):
    await _seed(container, config_service)
    service = _service(container)

    first = await service.quick_model(
        "acc1", provider="openai", api_base="https://api.deepseek.com/v1/",
        api_key="sk-test", model="deepseek-chat",
    )
    second = await service.quick_model(
        "acc1", provider="openai", api_base="https://api.deepseek.com/v1",
        api_key="sk-test", model="deepseek-chat",
    )

    assert first["preset_id"] == second["preset_id"], "同 provider+api_base 应复用预设"
    assert first["model_id"] == second["model_id"], "同名模型应复用"
    runtime = container.get(AgentManager).get_runtime("acc1")
    assert runtime.config.get("provider_model_pool").count(first["model_id"]) == 1


async def test_quick_model_validates_input(container, config_service):
    await _seed(container, config_service)
    service = _service(container)

    try:
        await service.quick_model("", api_base="https://x/v1", model="m")
        raise AssertionError("缺少账号应报错")
    except SetupError as e:
        assert "账号" in str(e)

    try:
        await service.quick_model("acc1", api_base="", model="m")
        raise AssertionError("缺少 api_base 应报错")
    except SetupError as e:
        assert "URL" in str(e)

    try:
        await service.quick_model("acc1", api_base="https://x/v1", model="")
        raise AssertionError("缺少模型应报错")
    except SetupError as e:
        assert "模型" in str(e)


async def test_setup_api_rejects_bad_payload_without_writing(container, config_service):
    """API 层只做形状校验（不落库）：缺 account_id / 坏 JSON 必须 400。"""
    client = TestClient(create_app(container))

    resp = client.post("/api/setup/quick-model", json={"model": "m"})
    assert resp.status_code == 400
    assert resp.json()["code"] == "SETUP_ACCOUNT_REQUIRED"

    bad = client.post(
        "/api/setup/quick-model",
        content=b"{oops",
        headers={"Content-Type": "application/json"},
    )
    assert bad.status_code == 400
    assert bad.json()["code"] == "SETUP_BAD_JSON"
    assert config_service.list_provider_presets() == []

async def test_api_default_account_is_unique(container, config_service):
    """API 层：连续保存两次，只应有一个账号。"""
    from starlette.testclient import TestClient

    from app.webui.app import create_app

    client = TestClient(create_app(container))
    first = client.post("/api/setup/default-account", json={
        "id": "default", "name": "主号", "ws_url": "127.0.0.1:4001/ws",
    })
    assert first.status_code == 200, first.text
    assert first.json()["account_id"] == "default"

    second = client.post("/api/setup/default-account", json={
        "id": "default", "name": "改个名", "ws_url": "127.0.0.1:4001/ws",
    })
    assert second.status_code == 200, second.text
    assert len(config_service.get_bots()) == 1

    listed = client.get("/api/setup/default-account")
    assert listed.status_code == 200
    assert listed.json()["account"]["name"] == "改个名"
    assert listed.json()["default_address"] == "127.0.0.1:4001/ws"
