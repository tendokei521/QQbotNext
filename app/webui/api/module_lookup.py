"""模块安装/查找的小工具（从 `modules.py` 拆出，控制单文件行数）。

只放**无状态**的纯辅助：上传落盘、编目查找、内置判断、物理目录兜底。
路由仍留在 `modules.py`，这样"装/卸"的完整流程一眼能顺着读下去。
"""

from __future__ import annotations

import os
import shutil
import tempfile
from contextlib import contextmanager

from fastapi import UploadFile
from fastapi.responses import JSONResponse

from app.core.logger import logger

#: 安装/预检异常缺省的可操作建议（异常自带 hint 时优先用异常里的）
_INSTALL_ERROR_HINT = (
    "请检查压缩包结构：插件根目录（或唯一一层子目录）下需要直接包含 module.json 与 main.py；"
    "可先用「预检」查看具体原因。"
)


def _install_error(exc: Exception) -> JSONResponse:
    """安装链路已知异常统一映射为 400 + hint（修 P0-6：不再以 500 暴露给前端）。"""
    hint = str(getattr(exc, "hint", "") or "").strip() or _INSTALL_ERROR_HINT
    message = str(exc).strip() or exc.__class__.__name__
    return JSONResponse(
        status_code=400,
        content={"status": "error", "message": message, "hint": hint},
    )


@contextmanager
def _uploaded_zip(file: UploadFile):
    """把上传文件落到临时 zip，结束后必删（安装/预检共用）。"""
    fd, tmp_path = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    try:
        with open(tmp_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
        yield tmp_path
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def _catalog_entry(registry, module_name: str):
    """按名取编目条目；registry 尚未实现 catalog_entries() 时返回 None（向后兼容）。"""
    lister = getattr(registry, "catalog_entries", None)
    if not callable(lister):
        return None
    try:
        for entry in lister() or []:
            if getattr(entry, "name", None) == module_name:
                return entry
    except Exception as e:  # noqa: BLE001 —— 编目不可用不应让管理接口整体失败
        logger.warning(f"[Module] 读取模块编目失败: {e}")
    return None


def _entry_is_builtin(entry) -> bool:
    if entry is None:
        return False
    if bool(getattr(entry, "builtin", False)):
        return True
    return str(getattr(entry, "source", "") or "").lower() == "builtin"


def _module_dir_exists(registry, module_name: str) -> bool:
    """物理目录存在性兜底判断（编目不可用的旧实现下也能正确回 404）。"""
    resolver = getattr(registry, "_resolve_module_dir", None)
    if not callable(resolver):
        return False
    for source in ("local", "zip"):
        try:
            path = resolver(module_name, source)
        except Exception as e:  # noqa: BLE001 —— 解析失败按不存在处理
            logger.debug(f"[Module] 解析 {module_name} 目录失败（按不存在处理）: {e}")
            continue
        if path is not None and getattr(path, "is_dir", lambda: False)():
            return True
    return False


def _module_exists(registry, module_name: str) -> bool:
    return _catalog_entry(registry, module_name) is not None or _module_dir_exists(registry, module_name)


def find_schema_field(module, field_type: str, endpoint: str) -> dict | None:
    """在模块 config_schema 中查找 type 与 endpoint 匹配的字段定义（返回副本，不污染类级 schema）。"""
    schema = getattr(module, "config_schema", None) or {}
    for key, field in schema.items():
        if not isinstance(field, dict):
            continue
        if field.get("type") == field_type and field.get("endpoint") == endpoint:
            result = dict(field)
            result["key"] = key
            return result
    return None


def conn_for_account(gateway, account_id: str | None):
    """按账号 id 取当前连接（路由里的 ``bot_id`` 参数实际是 account_id，不是 QQ 号）。

    历史 bug：用 ``find_conn_by_bot_id(account_id)`` 按 QQ 号匹配（字符串 vs 整数
    永不相等）→ 拿 None，热重载后实例 ``ctx.bot=None``、``online=False``、定时任务
    不注册。``get_bot(account_id)`` 才是按 ``connections`` 的账号 id 查；
    对只有旧接口的测试替身保留一次回退，真实网关不会走回退。
    """
    if not account_id:
        return None
    getter = getattr(gateway, "get_bot", None)
    conn = getter(account_id) if callable(getter) else None
    if conn is not None:
        return conn
    finder = getattr(gateway, "find_conn_by_bot_id", None)
    return finder(account_id) if callable(finder) else None


def resolve_bot(gateway, bot_id: str | None):
    """路由层按账号解析连接（空账号返回 None）。"""
    return conn_for_account(gateway, bot_id)


