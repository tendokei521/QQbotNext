"""模块配置保存：必须 await 落库，失败不能让接口回「已更新」。

历史问题：`update_config` 走同步 `module.config.save()`（fire-and-forget），
DB 写失败只留一行日志，接口照样 200 + 广播，前端显示"已保存"但重启后配置没了。

修法：改 `await config_service.save_binding(...)`（受锁写路径，失败抛出），
失败返回 5xx 且不广播。不走 `config.save_async()`：那条路会经
`save_module_config` 再广播一条未脱敏的重复事件。
"""

from __future__ import annotations

from app.webui.api import modules


class _Config:
    config_schema: dict = {}

    def __init__(self) -> None:
        self.raw_config: dict = {"greet": "hi"}

    def set(self, key, value, auto_save: bool = True) -> None:
        self.raw_config[key] = value


class _Module:
    name = "演示模块"
    config_schema: dict = {}

    def __init__(self) -> None:
        self.config = _Config()
        self.module_name = "demo"


class _ConfigService:
    def __init__(self) -> None:
        self.saved: tuple | None = None
        self.fail = False

    async def save_binding(self, module, scope_type, scope_id, **fields):
        if self.fail:
            raise RuntimeError("disk I/O error")
        self.saved = (module, scope_type, scope_id, dict(fields.get("config") or {}))


class _ReqJson:
    def __init__(self, payload: dict | None) -> None:
        self.payload = payload

    async def json(self):
        return self.payload


def _patch(monkeypatch, module: _Module, config_service: _ConfigService) -> list[str]:
    monkeypatch.setattr(modules, "_resolve_module", lambda *args, **kwargs: module)
    sent: list[str] = []

    async def fake_broadcast(message: str) -> None:
        sent.append(message)

    monkeypatch.setattr(modules.manager, "broadcast", fake_broadcast)
    return sent


async def _save(payload: dict | None, config_service: _ConfigService):
    return await modules.update_config(
        "demo",
        _ReqJson(payload),
        bot_id="acc1",
        resource_registry=None,
        agent_manager=None,
        registry=None,
        config_service=config_service,
    )


async def test_update_config_awaits_persist_before_success(monkeypatch):
    module = _Module()
    config_service = _ConfigService()
    sent = _patch(monkeypatch, module, config_service)

    resp = await _save({"greet": "hello"}, config_service)

    assert resp.status_code == 200
    assert config_service.saved == ("demo", "account", "acc1", {"greet": "hello"}), (
        "返回 200 之前必须已落库（且落到 account 作用域）"
    )
    assert len(sent) == 1


async def test_update_config_returns_5xx_when_persist_fails(monkeypatch):
    module = _Module()
    config_service = _ConfigService()
    config_service.fail = True
    sent = _patch(monkeypatch, module, config_service)

    resp = await _save({"greet": "hello"}, config_service)

    assert resp.status_code == 500, "落库失败必须让前端知道，不能回「已更新」"
    assert sent == [], "失败时不能广播成功事件"
    body = resp.body.decode("utf-8")
    assert "保存失败" in body
