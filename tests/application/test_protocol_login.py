"""P5-3：扫码登录转发 + 登录后自动回填账号。

锁定：只接受本机协议端地址、连不上时给可操作提示、未登录不写配置、登录后写反向 WS。
"""

from __future__ import annotations

import base64

import pytest

from app.application.protocol.login import (
    DEFAULT_REVERSE_WS,
    ProtocolLoginError,
    ProtocolLoginService,
    decode_qr_payload,
)


def _service(qr=None, status=None, error=None) -> ProtocolLoginService:
    def get(url):
        if error:
            raise OSError(error)
        if "Qrcode" in url:
            return qr if qr is not None else {"data": {"qrcode": base64.b64encode(b"png").decode()}}
        return status if status is not None else {"data": {"is_online": True, "uin": 10001, "nick": "小号"}}

    return ProtocolLoginService(http_get=get)


def test_rejects_non_local_base_url():
    with pytest.raises(ProtocolLoginError) as exc:
        ProtocolLoginService("http://example.com:6099")
    assert exc.value.code == "PROTOCOL_BASE_URL_INVALID"


def test_qrcode_returns_data_url():
    result = _service().qrcode()
    assert result["image"].startswith("data:image/png;base64,")
    assert decode_qr_payload(result["image"]) == b"png"


def test_qrcode_accepts_qrcode_url_only():
    service = _service(qr={"data": {"qrcode_url": "https://qr.example/x"}})
    result = service.qrcode()
    assert result["content"] == "https://qr.example/x"
    assert result["image"] == ""


def test_unreachable_protocol_gives_actionable_hint():
    service = _service(error="connection refused")
    with pytest.raises(ProtocolLoginError) as exc:
        service.qrcode()
    assert exc.value.code == "PROTOCOL_UNREACHABLE"
    assert "6099" in exc.value.hint


def test_status_parsing():
    state = _service().status()
    assert state == {"online": True, "user_id": 10001, "nickname": "小号"}

    offline = _service(status={"data": {"is_online": False}}).status()
    assert offline["online"] is False


async def test_complete_login_writes_account(container, config_service):
    await config_service.save_bots([
        {"id": "acc1", "name": "主号", "ws_url": "ws://127.0.0.1:9999/", "conn_mode": "forward"},
    ])
    service = _service()

    result = await service.complete_login(config_service, "acc1", access_token="tok-1")

    assert result["ws_url"] == DEFAULT_REVERSE_WS
    bot = config_service.get_bots()[0]
    # 存储层会把 access_token 拼进 ws_url 查询串（见 ws_url.py），这里按前缀断言
    assert bot["ws_url"].startswith(DEFAULT_REVERSE_WS)
    assert "access_token=tok-1" in bot["ws_url"]
    assert bot["conn_mode"] == "reverse"


async def test_complete_login_requires_online(container, config_service):
    await config_service.save_bots([
        {"id": "acc1", "name": "主号", "ws_url": "ws://127.0.0.1:3001/"},
    ])
    service = _service(status={"data": {"is_online": False}})

    with pytest.raises(ProtocolLoginError) as exc:
        await service.complete_login(config_service, "acc1")
    assert exc.value.code == "PROTOCOL_NOT_LOGGED_IN"


async def test_complete_login_unknown_account(container, config_service):
    service = _service()
    with pytest.raises(ProtocolLoginError) as exc:
        await service.complete_login(config_service, "nope")
    assert exc.value.code == "PROTOCOL_ACCOUNT_MISSING"
