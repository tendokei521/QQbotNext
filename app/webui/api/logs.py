"""日志 API：最近日志读取 + 日志导出（当前/历史归档，单文件与 ZIP）。"""

from __future__ import annotations

import os
import re
import tempfile
import time
import zipfile
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from app.webui.api.deps import get_container

router = APIRouter(tags=["logs"])

LOG_FILENAMES = ("debug.log", "warn.log", "errors.log", "user.log")
_ARCHIVE_FOLDER_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{2}$")


@router.get("/logs")
async def api_logs(request: Request):
    from app.infrastructure.config.config_service import ConfigService
    from app.services.log_service import LogService

    container = get_container(request)
    config = container.get(ConfigService).get_webui_config()
    levels = config.get("logs", {}).get("visible_levels", ["info", "warning", "error"])
    max_lines = config.get("logs", {}).get("max_lines", 50)

    mode = request.query_params.get("mode", "")
    if mode not in ("simple", "raw"):
        mode = "raw" if config.get("logs", {}).get("show_raw_logs", False) else "simple"
    source = "user" if mode == "simple" else "debug"
    return JSONResponse(content=container.get(LogService).get_recent_logs(max_lines, levels, source=source))


# ==================== 日志导出 ====================


def _log_dir(request: Request) -> Path:
    from app.services.log_service import LogService

    container = get_container(request)
    return Path(container.get(LogService).log_dir)


def _file_info(path: Path) -> dict:
    stat = path.stat()
    return {
        "name": path.name,
        "size": stat.st_size,
        "mtime": int(stat.st_mtime),
    }


def _safe_resolve(log_dir: Path, folder: str, filename: str) -> Path:
    if folder:
        folder = str(folder or "").strip()
        if not _ARCHIVE_FOLDER_RE.match(folder):
            raise ValueError(f"非法归档目录: {folder}")
        base = log_dir / folder
    else:
        base = log_dir
    filename = str(filename or "").strip()
    if filename not in LOG_FILENAMES:
        raise ValueError(f"非法日志文件名: {filename}")
    target = base / filename
    resolved = target.resolve()
    log_root = log_dir.resolve()
    if not resolved.is_relative_to(log_root):
        raise ValueError("路径越界")
    return target


@router.get("/logs/export/list")
async def api_log_export_list(request: Request):
    log_dir = _log_dir(request)
    current = []
    for name in LOG_FILENAMES:
        p = log_dir / name
        if p.exists() and p.is_file():
            current.append(_file_info(p))

    archives = []
    if log_dir.exists():
        for item in sorted(log_dir.iterdir()):
            if not item.is_dir() or not _ARCHIVE_FOLDER_RE.match(item.name):
                continue
            files = []
            for name in LOG_FILENAMES:
                p = item / name
                if p.exists() and p.is_file():
                    files.append(_file_info(p))
            if files:
                archives.append({"folder": item.name, "files": files})
    return JSONResponse(content={
        "ok": True,
        "logs_dir": str(log_dir),
        "current": current,
        "archives": archives,
    })


@router.get("/logs/export/download")
async def api_log_export_download(request: Request, folder: str = "", file: str = ""):
    try:
        log_dir = _log_dir(request)
        path = _safe_resolve(log_dir, folder, file)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})
    if not path.exists() or not path.is_file():
        return JSONResponse(status_code=404, content={"status": "error", "message": "日志文件不存在"})
    filename = f"{folder}_{file}" if folder else file
    return StreamingResponse(
        open(path, "rb"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/logs/export/zip")
async def api_log_export_zip(request: Request):
    from app.core.logger import logger

    try:
        items = await request.json()
    except Exception:
        items = []
    if not isinstance(items, list) or not items:
        return JSONResponse(status_code=400, content={"status": "error", "message": "请选择至少一个日志文件"})

    log_dir = _log_dir(request)
    validated: list[tuple[Path, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            path = _safe_resolve(log_dir, str(item.get("folder", "") or ""), str(item.get("file", "") or ""))
        except ValueError as e:
            logger.warning(f"[LogExport] 跳过非法项: {e}")
            continue
        if path.exists() and path.is_file():
            folder = str(item.get("folder", "") or "")
            arcname = f"{folder}/{path.name}" if folder else path.name
            validated.append((path, arcname))
    if not validated:
        return JSONResponse(status_code=400, content={"status": "error", "message": "所选日志文件均不存在或非法"})

    fd, zip_path = tempfile.mkstemp(prefix="qqbot-logs-", suffix=".zip")
    os.close(fd)
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for path, arcname in validated:
                zf.write(path, arcname)
    except Exception as e:
        os.unlink(zip_path)
        return JSONResponse(status_code=500, content={"status": "error", "message": f"打包失败: {e}"})

    filename = f"qqbot-logs-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    return FileResponse(
        zip_path,
        filename=filename,
        media_type="application/zip",
        background=BackgroundTask(os.unlink, zip_path),
    )
