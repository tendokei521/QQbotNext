"""孤儿账号数据备份必须是原子写（tmp + os.replace）。

历史问题（`account_store._write_orphan_backup`）：直接 ``path.write_text``，
进程在写一半时退出会留下半个 JSON——下次排查孤儿数据时读到损坏文件，
还会被误当成"备份已存在"。
"""

from __future__ import annotations

import json
import os

from app.infrastructure.config.config_service import ConfigService
from app.infrastructure.persistence.database import Database


async def _open_config(tmp_path):
    db = Database(tmp_path / "backup.db")
    await db.connect()
    root = tmp_path / "root"
    config_service = ConfigService(db, root)
    await config_service.init()
    return db, config_service, root / "data" / "orphan-account-data.json"


async def test_orphan_backup_failure_keeps_previous_file(tmp_path, monkeypatch):
    """替换失败（如断电/权限）→ 旧备份文件保持完整，不留下半个新文件。"""
    db, config_service, backup = await _open_config(tmp_path)
    try:
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_text('{"accounts": ["previous"]}', encoding="utf-8")

        def _boom(_src, _dst):
            raise OSError("模拟替换时崩溃")

        monkeypatch.setattr(os, "replace", _boom)

        assert config_service._write_orphan_backup({"accounts": ["ghost"]}) is None
        assert json.loads(backup.read_text(encoding="utf-8"))["accounts"] == ["previous"]
        assert not backup.with_name(backup.name + ".tmp").exists(), "失败后要清理临时文件"
    finally:
        await db.close()
