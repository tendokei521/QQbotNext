"""模块安装来源台账：哪个**预设**装了某个模块。

模块是全局安装的（不分账号），所以按模块名记"最后一次安装来源"：
- 预设应用时装/升级模块 → ``record_origin``（action=installed / upgraded）；
- 回滚/卸载把模块删掉 → ``clear_origin``；
- 模块页 / 预设详情读 ``load_origins`` 显示「来自预设「xxx」」。

注意：手动「安装 zip」不写台账（来源 = 本地），台账里没有行就是"本地安装"。
"""

from __future__ import annotations

import time

SQL_UPSERT = (
    "INSERT OR REPLACE INTO module_origins "
    "(module_name, bundle_id, bundle_name, bundle_version, account_id, action, installed_at) "
    "VALUES (?,?,?,?,?,?,?)"
)
SQL_CLEAR = "DELETE FROM module_origins WHERE module_name = ?"
SQL_LIST = "SELECT * FROM module_origins ORDER BY installed_at DESC"


async def record_origin(
    db,
    module_name: str,
    *,
    bundle_id: str,
    bundle_name: str = "",
    bundle_version: str = "",
    account_id: str = "",
    action: str = "installed",
) -> None:
    """记/更新一个模块的来源（同一模块后装的覆盖先装的）。"""
    await db.execute(SQL_UPSERT, (
        str(module_name),
        str(bundle_id),
        str(bundle_name or ""),
        str(bundle_version or ""),
        str(account_id or ""),
        str(action or "installed"),
        int(time.time()),
    ))


async def clear_origin(db, module_name: str) -> None:
    await db.execute(SQL_CLEAR, (str(module_name),))


async def load_origins(db) -> dict[str, dict]:
    """``{module_name: {bundle_id, bundle_name, bundle_version, account_id, action, installed_at}}``。"""
    rows = await db.fetchall(SQL_LIST)
    return {
        str(row["module_name"]): {
            "bundle_id": row["bundle_id"],
            "bundle_name": row["bundle_name"],
            "bundle_version": row["bundle_version"],
            "account_id": row["account_id"],
            "action": row["action"],
            "installed_at": row["installed_at"],
        }
        for row in (rows or [])
    }
