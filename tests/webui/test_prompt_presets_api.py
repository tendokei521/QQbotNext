"""系统提示词方案 API：设置页读三套方案、保存覆盖、恢复默认。"""

from __future__ import annotations

from starlette.testclient import TestClient

from app.application import prompt_preset_service
from app.agent.assembly import presets
from app.webui.app import create_app


def _client(container) -> TestClient:
    return TestClient(create_app(container))


async def test_list_returns_three_presets(container, config_service):
    client = _client(container)

    resp = client.get("/api/prompt-presets")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert [row["key"] for row in body["presets"]] == ["chat", "roleplay", "assistant"]
    assert all(row["default_text"].strip() for row in body["presets"])


async def test_save_and_reset_override(container, config_service):
    client = _client(container)
    try:
        saved = client.put("/api/prompt-presets/chat", json={"text": "自定义系统提示词"})

        assert saved.status_code == 200, saved.text
        row = next(r for r in saved.json()["presets"] if r["key"] == "chat")
        assert row["customized"] is True
        assert row["effective_text"] == "自定义系统提示词"

        reset = client.delete("/api/prompt-presets/chat")

        assert reset.status_code == 200, reset.text
        row = next(r for r in reset.json()["presets"] if r["key"] == "chat")
        assert row["customized"] is False
        assert row["effective_text"] == row["default_text"]
    finally:
        for key in presets.keys():
            await prompt_preset_service.reset_override(config_service, key)


async def test_unknown_key_returns_400(container):
    client = _client(container)
    resp = client.put("/api/prompt-presets/nope", json={"text": "x"})
    assert resp.status_code == 400
    assert "没有这套提示词方案" in resp.json()["message"]


async def test_bad_json_returns_400(container):
    client = _client(container)
    resp = client.put(
        "/api/prompt-presets/chat",
        content=b"{oops",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400
