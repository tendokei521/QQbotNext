"""预设 API：校验 / 导入登记 / 列表 / 下载 / 从账号导出 / 应用 / 回滚。

P2 的端点不改运行配置；P3 起新增 `apply` / `rollback`（写账号配置走单事务 + 快照）。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from app.application.agent_metadata import agent_config_schema
from app.application.bundle.errors import BundleError
from app.application.bundle.service import BundleService
from app.core.logger import logger
from app.webui.api.deps import (
    get_agent_manager,
    get_bot_service,
    get_config_service,
    get_module_registry,
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


def _module_catalog(registry) -> dict[str, dict]:
    """模块编目 → ``{name: {version, builtin, path}}``（导出打包 + 依赖判定共用）。"""
    if registry is None:
        return {}
    try:
        entries = registry.catalog_entries()
    except Exception as e:  # noqa: BLE001 —— 编目不可用不该让预设接口 500
        logger.warning(f"[Bundle] 读取模块编目失败（按空目录处理）: {e}")
        return {}
    catalog: dict[str, dict] = {}
    for entry in entries:
        name = str(getattr(entry, "name", "") or "")
        if not name:
            continue
        catalog[name] = {
            "version": str(getattr(entry, "version", "") or "0.0.0"),
            "builtin": bool(getattr(entry, "builtin", True)),
            "path": str(getattr(entry, "path", "") or ""),
            "display_name": str(getattr(entry, "display_name", "") or name),
            # 软卸载（tombstone）的模块视为"未安装"：随包安装会顺带清掉 tombstone
            "status": str(getattr(entry, "status", "") or ""),
        }
    return catalog


class _RegistryModuleInstaller:
    """把随包模块接到**既有 zip 安装通道**（不新增执行面）。

    安装/卸载都走 `ModuleRegistry`：成功后会为已存在账号补齐模块实例；
    卸载只删外部插件目录（配置与数据保留），内置模块直接跳过。
    """

    def __init__(self, registry, bot_service) -> None:
        self.registry = registry
        self.bot_service = bot_service

    async def install(self, name: str, data: bytes) -> dict:
        fd, path = tempfile.mkstemp(suffix=".zip", prefix="bundle-module-")
        os.close(fd)
        try:
            Path(path).write_bytes(data)
            info = await self.registry.install_from_zip(path, force=True)
        finally:
            try:
                Path(path).unlink()
            except OSError as e:  # noqa: BLE001 —— 临时文件清理失败不影响安装结果
                logger.debug(f"[Bundle] 清理临时模块包失败: {e}")
        ensure = getattr(self.bot_service, "ensure_module_instances", None)
        if callable(ensure):
            try:
                await ensure(name)
            except Exception as e:  # noqa: BLE001 —— 补实例失败不影响安装结果
                logger.warning(f"[Bundle] 模块 {name} 安装后补齐账号实例失败: {e}")
        return info or {}

    async def uninstall(self, name: str) -> None:
        try:
            entry = self.registry.catalog.get(name)
        except Exception:  # noqa: BLE001 —— 编目不可用按"不存在"处理
            entry = None
        if entry is not None and getattr(entry, "builtin", False):
            return
        await self.registry.delete_module(name, purge_data=False)


def _service(
    config_service,
    agent_manager,
    bot_service,
    settings,
    registry=None,
) -> BundleService:
    module_dir = Path(settings.module_dir)
    data_root = module_dir.resolve().parent if module_dir.name == "module" else module_dir
    catalog = _module_catalog(registry)
    return BundleService(
        config_service,
        data_root=data_root,
        agent_manager=agent_manager,
        installed_modules={
            name: item["version"]
            for name, item in catalog.items()
            if item.get("status") != "uninstalled"
        },
        module_catalog=catalog,
        module_installer=(
            _RegistryModuleInstaller(registry, bot_service) if registry is not None else None
        ),
        agent_schema=agent_config_schema(),
    )


async def _read_upload(file: UploadFile) -> bytes:
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise BundleError("BUNDLE_ZIP_TOO_LARGE", "预设包超过 200 MB")
    return data


@router.post("/bundles/validate")
async def api_bundles_validate(
    file: UploadFile = File(...),
    config_service=Depends(get_config_service),
    agent_manager=Depends(get_agent_manager),
    bot_service=Depends(get_bot_service),
    settings=Depends(get_settings),
    registry=Depends(get_module_registry),
):
    try:
        data = await _read_upload(file)
    except BundleError as e:
        return _err(400, str(e), e.code, e.hint)
    try:
        report = _service(config_service, agent_manager, bot_service, settings, registry).validate(data)
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
    registry=Depends(get_module_registry),
):
    try:
        data = await _read_upload(file)
        result = await _service(
            config_service, agent_manager, bot_service, settings, registry
        ).import_bundle(data)
    except BundleError as e:
        return _err(400, str(e), e.code, e.hint)
    return _ok("预设已导入（尚未应用到账号）", **result)


@router.post("/bundles/import-apply")
async def api_bundles_import_apply(
    file: UploadFile = File(...),
    account_id: str = Form(...),
    secrets: str = Form("{}"),
    config_service=Depends(get_config_service),
    agent_manager=Depends(get_agent_manager),
    bot_service=Depends(get_bot_service),
    settings=Depends(get_settings),
    registry=Depends(get_module_registry),
):
    """一键：导入登记 + 立即应用到账号（失败保留工件，返回 ``imported=true`` 便于重试）。"""
    account_id = str(account_id or "").strip()
    if not account_id:
        return _err(400, "缺少 account_id", "BUNDLE_ACCOUNT_REQUIRED")
    try:
        secrets_obj = json.loads(secrets or "{}")
    except json.JSONDecodeError as e:
        return _err(400, f"secrets 不是合法 JSON: {e}", "BUNDLE_BAD_SECRETS")
    if not isinstance(secrets_obj, dict):
        return _err(400, "secrets 必须是 JSON 对象", "BUNDLE_BAD_SECRETS")

    service = _service(config_service, agent_manager, bot_service, settings, registry)
    try:
        data = await _read_upload(file)
        result = await service.import_bundle(data)
    except BundleError as e:
        return _err(400, str(e), e.code, e.hint)

    bundle = result.get("bundle") or {}
    try:
        applied = await service.apply(
            str(bundle.get("id") or ""), account_id, secrets=secrets_obj
        )
    except BundleError as e:
        # 工件已登记：前端可以提示"已导入，应用失败"，让用户补密钥/修问题后重试
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "code": e.code,
                "message": str(e),
                "hint": e.hint,
                "imported": True,
                "bundle": bundle,
            },
        )
    return _ok("预设已导入并应用到账号", **result, apply=applied)


@router.get("/bundles")
async def api_bundles_list(
    config_service=Depends(get_config_service),
    agent_manager=Depends(get_agent_manager),
    bot_service=Depends(get_bot_service),
    settings=Depends(get_settings),
    registry=Depends(get_module_registry),
):
    bundles = _service(config_service, agent_manager, bot_service, settings, registry).list_installed()
    return _ok(bundles=bundles, total=len(bundles))


@router.get("/bundles/{bundle_id}/download")
async def api_bundles_download(
    bundle_id: str,
    config_service=Depends(get_config_service),
    agent_manager=Depends(get_agent_manager),
    bot_service=Depends(get_bot_service),
    settings=Depends(get_settings),
    registry=Depends(get_module_registry),
):
    try:
        path = _service(
            config_service, agent_manager, bot_service, settings, registry
        ).bundle_file(bundle_id)
    except BundleError as e:
        return _err(400, str(e), e.code, e.hint)
    if path is None:
        return _err(404, "预设不存在或已被删除", "BUNDLE_NOT_FOUND")
    return Response(
        content=path.read_bytes(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(path.name)}"},
    )


@router.get("/bundles/export-options")
async def api_bundles_export_options(
    account_id: str | None = None,
    config_service=Depends(get_config_service),
    agent_manager=Depends(get_agent_manager),
    bot_service=Depends(get_bot_service),
    settings=Depends(get_settings),
    registry=Depends(get_module_registry),
):
    """导出对话框的模块清单（名称/版本/内置/体积/启停），供勾选"打包哪些模块"。"""
    if not account_id:
        return _err(400, "缺少 account_id", "BUNDLE_ACCOUNT_REQUIRED")
    options = _service(
        config_service, agent_manager, bot_service, settings, registry
    ).export_options(account_id)
    return _ok(modules=options, total=len(options))


@router.get("/bundles/export")
async def api_bundles_export(
    account_id: str | None = None,
    bundle_id: str | None = None,
    name: str | None = None,
    version: str = "1.0.0",
    description: str = "",
    modules: str | None = None,
    config_service=Depends(get_config_service),
    agent_manager=Depends(get_agent_manager),
    bot_service=Depends(get_bot_service),
    settings=Depends(get_settings),
    registry=Depends(get_module_registry),
):
    if not account_id:
        return _err(400, "缺少 account_id", "BUNDLE_ACCOUNT_REQUIRED")
    module_filter = None
    if modules is not None:
        module_filter = [item.strip() for item in modules.split(",") if item.strip()]
    try:
        data = _service(
            config_service, agent_manager, bot_service, settings, registry
        ).export_account(
            account_id,
            bundle_id=str(bundle_id or f"account-{account_id}"),
            name=str(name or f"账号 {account_id} 的配置"),
            version=str(version or "1.0.0"),
            description=str(description or ""),
            module_filter=module_filter,
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


# ==================== 应用 / 回滚（P3） ====================


@router.post("/bundles/{bundle_id}/apply")
async def api_bundles_apply(
    bundle_id: str,
    request: Request,
    config_service=Depends(get_config_service),
    agent_manager=Depends(get_agent_manager),
    bot_service=Depends(get_bot_service),
    settings=Depends(get_settings),
    registry=Depends(get_module_registry),
):
    """把已导入的预设应用到某账号（事务写 + 快照；失败自动回滚不落半套）。"""
    try:
        body = await request.json()
    except Exception as e:  # noqa: BLE001
        return _err(400, f"请求体不是合法 JSON: {e}", "BUNDLE_BAD_JSON")
    if not isinstance(body, dict):
        return _err(400, "请求体必须是 JSON 对象", "BUNDLE_BAD_BODY")
    account_id = str(body.get("account_id") or "").strip()
    if not account_id:
        return _err(400, "缺少 account_id", "BUNDLE_ACCOUNT_REQUIRED")
    secrets = body.get("secrets") if isinstance(body.get("secrets"), dict) else {}
    try:
        result = await _service(
            config_service, agent_manager, bot_service, settings, registry
        ).apply(bundle_id, account_id, secrets=secrets)
    except BundleError as e:
        return _err(400, str(e), e.code, e.hint)
    return _ok("已应用到账号", **result)


@router.post("/activations/{activation_id}/rollback")
async def api_bundles_rollback(
    activation_id: str,
    config_service=Depends(get_config_service),
    agent_manager=Depends(get_agent_manager),
    bot_service=Depends(get_bot_service),
    settings=Depends(get_settings),
    registry=Depends(get_module_registry),
):
    try:
        result = await _service(
            config_service, agent_manager, bot_service, settings, registry
        ).rollback(activation_id)
    except BundleError as e:
        return _err(400, str(e), e.code, e.hint)
    return _ok("已回滚", **result)


@router.get("/bundles/activations")
async def api_bundles_activations(
    account_id: str | None = None,
    config_service=Depends(get_config_service),
    agent_manager=Depends(get_agent_manager),
    bot_service=Depends(get_bot_service),
    settings=Depends(get_settings),
    registry=Depends(get_module_registry),
):
    activations = await _service(
        config_service, agent_manager, bot_service, settings
    ).list_activations(account_id)
    return _ok(activations=activations, total=len(activations))


@router.post("/bundles/{bundle_id}/uninstall")
async def api_bundles_uninstall(
    bundle_id: str,
    request: Request,
    config_service=Depends(get_config_service),
    agent_manager=Depends(get_agent_manager),
    bot_service=Depends(get_bot_service),
    settings=Depends(get_settings),
    registry=Depends(get_module_registry),
):
    """卸载预设：回滚其激活 + 清理记账，无引用时删除工件文件。"""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 —— 允许空 body（卸载全部账号的激活）
        body = {}
    account_id = str((body or {}).get("account_id") or "").strip() or None
    try:
        result = await _service(
            config_service, agent_manager, bot_service, settings, registry
        ).uninstall(bundle_id, account_id)
    except BundleError as e:
        return _err(400, str(e), e.code, e.hint)
    return _ok("预设已卸载", **result)
