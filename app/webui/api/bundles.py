"""套装 API（P2）：校验 / 导入登记 / 列表 / 下载 / 从账号导出。

本阶段**不应用**到账号（应用与回滚在 P3），所以这些端点不会改任何运行配置。
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import JSONResponse, Response

from app.application.agent_metadata import agent_config_schema
from app.application.bundle.errors import BundleError
from app.application.bundle.service import BundleService
from app.webui.api.deps import (
    get_agent_manager,
    get_bot_service,
    get_config_service,
    get_settings,
)

router = APIRouter(tags=["bundles"])

#: 上传体积上限（与校验器一致；先在 API 层挡一次，避免把超大文件读进内存）
MAX_UPLOAD_BYTES = 200 * 1024 * 1024


def _ok(message: str = "", **extra):
    return JSONResponse(content={"ok": True, "message": message, **extra})


def _err(status: int, message: str, code: str = "", hint: str = ""):
    body = {"ok": False, "message": message, "code": code}
    if hint:
        body["hint"] = hint
    return JSONResponse(status_code=status, content=body)


def _installed_modules(bot_service) -> dict[str, str]:
    registry = getattr(bot_service, "registry", None)
    if registry is None:
        return {}
    try:
        entries = registry.catalog_entries()
    except Exception:  # noqa: BLE001 —— 编目不可用不该让套装接口 500
        return {}
    modules: dict[str, str] = {}
    for entry in entries:
        name = str(getattr(entry, "name", "") or "")
        if name:
            modules[name] = str(getattr(entry, "version", "") or "0.0.0")
    return modules


def _service(
    config_service,
    agent_manager,
    bot_service,
    settings,
) -> BundleService:
    module_dir = Path(settings.module_dir)
    data_root = module_dir.resolve().parent if module_dir.name == "module" else module_dir
    return BundleService(
        config_service,
        data_root=data_root,
        agent_manager=agent_manager,
        installed_modules=_installed_modules(bot_service),
        agent_schema=agent_config_schema(),
    )


async def _read_upload(file: UploadFile) -> bytes:
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise BundleError("BUNDLE_ZIP_TOO_LARGE", "套装包超过 200 MB")
    return data


@router.post("/bundles/validate")
async def api_bundles_validate(
    file: UploadFile = File(...),
    config_service=Depends(get_config_service),
    agent_manager=Depends(get_agent_manager),
    bot_service=Depends(get_bot_service),
    settings=Depends(get_settings),
):
    try:
        data = await _read_upload(file)
    except BundleError as e:
        return _err(400, str(e), e.code, e.hint)
    try:
        report = _service(config_service, agent_manager, bot_service, settings).validate(data)
    except BundleError as e:
        return _err(400, str(e), e.code, e.hint)
    return _ok("预检完成", **report)


@router.post("/bundles/import")
async def api_bundles_import(
    file: UploadFile = File(...),
    config_service=Depends(get_config_service),
    agent_manager=Depends(get_agent_manager),
    bot_service=Depends(get_bot_service),
    settings=Depends(get_settings),
):
    try:
        data = await _read_upload(file)
        result = await _service(
            config_service, agent_manager, bot_service, settings
        ).import_bundle(data)
    except BundleError as e:
        return _err(400, str(e), e.code, e.hint)
    return _ok("套装已导入（尚未应用到账号）", **result)


@router.get("/bundles")
async def api_bundles_list(
    config_service=Depends(get_config_service),
    agent_manager=Depends(get_agent_manager),
    bot_service=Depends(get_bot_service),
    settings=Depends(get_settings),
):
    bundles = _service(config_service, agent_manager, bot_service, settings).list_installed()
    return _ok(bundles=bundles, total=len(bundles))


@router.get("/bundles/{bundle_id}/download")
async def api_bundles_download(
    bundle_id: str,
    config_service=Depends(get_config_service),
    agent_manager=Depends(get_agent_manager),
    bot_service=Depends(get_bot_service),
    settings=Depends(get_settings),
):
    try:
        path = _service(
            config_service, agent_manager, bot_service, settings
        ).bundle_file(bundle_id)
    except BundleError as e:
        return _err(400, str(e), e.code, e.hint)
    if path is None:
        return _err(404, "套装不存在或已被删除", "BUNDLE_NOT_FOUND")
    return Response(
        content=path.read_bytes(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(path.name)}"},
    )


@router.get("/bundles/export")
async def api_bundles_export(
    account_id: str | None = None,
    bundle_id: str | None = None,
    name: str | None = None,
    version: str = "1.0.0",
    description: str = "",
    config_service=Depends(get_config_service),
    agent_manager=Depends(get_agent_manager),
    bot_service=Depends(get_bot_service),
    settings=Depends(get_settings),
):
    if not account_id:
        return _err(400, "缺少 account_id", "BUNDLE_ACCOUNT_REQUIRED")
    try:
        data = _service(
            config_service, agent_manager, bot_service, settings
        ).export_account(
            account_id,
            bundle_id=str(bundle_id or f"account-{account_id}"),
            name=str(name or f"账号 {account_id} 的配置"),
            version=str(version or "1.0.0"),
            description=str(description or ""),
        )
    except BundleError as e:
        return _err(400, str(e), e.code, e.hint)
    filename = f"{bundle_id or 'bundle'}-{version}.zip"
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            "X-Bundle-Id": str(bundle_id or f"account-{account_id}"),
        },
    )
