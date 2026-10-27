"""协议端 API：状态 / 安装 / 启停 / 日志 / 扫码登录（计划 P5-2、P5-3）。

所有端点都走 application 层服务（`ProtocolInstaller` / `ProtocolSupervisor` /
`ProtocolLoginService`），WebUI 只做 HTTP 适配。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.application.protocol.installer import ProtocolInstaller
from app.application.protocol.login import ProtocolLoginError, ProtocolLoginService
from app.application.protocol.manifest import ProtocolError
from app.application.protocol.supervisor import ProtocolProcessError, ProtocolSupervisor
from app.webui.api.deps import get_config_service

router = APIRouter(tags=["protocol"])

#: 支持的协议端（与清单里的 name 对应）
PROTOCOLS = ("napcat", "lagrange")


def _ok(message: str = "", **extra):
    return JSONResponse(content={"ok": True, "message": message, **extra})


def _err(status: int, message: str, code: str = "", hint: str = ""):
    body = {"ok": False, "message": message, "code": code}
    if hint:
        body["hint"] = hint
    return JSONResponse(status_code=status, content=body)


def _container(request: Request):
    from app.core.container import get_active

    container = getattr(request.app.state, "container", None)
    return container if container is not None else get_active()


def _installer(request: Request) -> ProtocolInstaller:
    return _container(request).get(ProtocolInstaller)


def _supervisor(request: Request) -> ProtocolSupervisor:
    return _container(request).get(ProtocolSupervisor)


def _login(request: Request) -> ProtocolLoginService:
    return _container(request).get(ProtocolLoginService)


@router.get("/protocol")
async def api_protocol_list(request: Request):
    """列出协议端：清单版本 + 安装/运行状态（面板首屏用它一次拿全）。"""
    installer = _installer(request)
    supervisor = _supervisor(request)
    manifest = installer.manifest()
    items = []
    for name in PROTOCOLS:
        entry = manifest["protocols"].get(name) or {}
        items.append({
            "protocol": name,
            "display_name": entry.get("display_name") or name,
            "homepage": entry.get("homepage") or "",
            "versions": [item["version"] for item in entry.get("versions") or []],
            "notes": {item["version"]: item.get("note", "") for item in entry.get("versions") or []},
            **supervisor.status(name),
        })
    return _ok(protocols=items)


@router.post("/protocol/{protocol}/install")
async def api_protocol_install(protocol: str, request: Request):
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 —— 版本可选，空 body 表示装清单里的最新 pin 版
        body = {}
    version = str((body or {}).get("version") or "").strip() or None
    try:
        result = _installer(request).install(protocol, version)
    except ProtocolError as e:
        return _err(400, str(e), e.code, e.hint)
    return _ok("协议端已安装", **result)


@router.post("/protocol/{protocol}/start")
async def api_protocol_start(protocol: str, request: Request):
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    args = (body or {}).get("args")
    try:
        status = _supervisor(request).start(protocol, list(args) if isinstance(args, list) else None)
    except ProtocolProcessError as e:
        return _err(400, str(e), e.code, e.hint)
    return _ok("已启动", **status)


@router.post("/protocol/{protocol}/stop")
async def api_protocol_stop(protocol: str, request: Request):
    try:
        status = _supervisor(request).stop(protocol)
    except ProtocolProcessError as e:
        return _err(400, str(e), e.code, e.hint)
    return _ok("已停止", **status)


@router.post("/protocol/{protocol}/restart")
async def api_protocol_restart(protocol: str, request: Request):
    try:
        status = _supervisor(request).restart(protocol)
    except ProtocolProcessError as e:
        return _err(400, str(e), e.code, e.hint)
    return _ok("已重启", **status)


@router.get("/protocol/{protocol}/status")
async def api_protocol_status(protocol: str, request: Request):
    return _ok(**_supervisor(request).status(protocol))


@router.get("/protocol/{protocol}/log")
async def api_protocol_log(protocol: str, request: Request, lines: int = 100):
    return _ok(log=_supervisor(request).tail(protocol, lines=min(max(lines, 1), 1000)))


@router.get("/protocol/{protocol}/qrcode")
async def api_protocol_qrcode(protocol: str, request: Request):
    try:
        result = _login(request).qrcode()
    except ProtocolLoginError as e:
        return _err(400, str(e), e.code, e.hint)
    return _ok(**result)


@router.get("/protocol/{protocol}/login-status")
async def api_protocol_login_status(protocol: str, request: Request):
    try:
        result = _login(request).status()
    except ProtocolLoginError as e:
        return _err(400, str(e), e.code, e.hint)
    return _ok(**result)


@router.post("/protocol/{protocol}/login/complete")
async def api_protocol_login_complete(protocol: str, request: Request, config_service=Depends(get_config_service)):
    """扫码成功后把协议端地址/令牌写进账号（此后面板就是"已登录"状态）。"""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    account_id = str((body or {}).get("account_id") or "").strip()
    if not account_id:
        return _err(400, "缺少 account_id", "PROTOCOL_ACCOUNT_REQUIRED")
    try:
        result = await _login(request).complete_login(
            config_service,
            account_id,
            ws_url=str((body or {}).get("ws_url") or "").strip() or "ws://127.0.0.1:3001/onebot/v11/ws",
            access_token=str((body or {}).get("access_token") or ""),
        )
    except ProtocolLoginError as e:
        return _err(400, str(e), e.code, e.hint)
    return _ok("登录成功，账号已自动配置", **result)
