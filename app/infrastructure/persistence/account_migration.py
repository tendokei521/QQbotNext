"""账号身份迁移：从「索引 + 登录后 QQ 号」升级到「用户设置的 account id」。

## 为什么要迁移

历史实现里账号有两套身份，都不适合当唯一凭据：

- ``bot_index``：数组下标。删掉中间账号后索引被整体前移，同一账号的身份就变了；
- ``bot_id``：**登录成功后**才能拿到的 QQ 号。登录前根本不存在，同一槽位换个 QQ
  登录就变成"另一个账号"，而且它是模块配置、会话、统计、磁盘文件名的事实 key。

现在统一成 ``account_id``：**添加账号时由用户填写**、创建后不可变、前后端只用它做判断。
``name`` 是给人看的展示名（日志与界面都用它），可以随时改。

## 存量数据怎么对齐

id 的回填规则刻意让旧数据"天然对上"：

- **登录过的账号 → 用它的 QQ 号当 id**。因为 ``module_config`` / ``module_authority``
  的既有 key 本来就是 QQ 号，这样那两张表一行都不用改；
- **从未登录过的账号 → 用 ``bot{N}``**。它们没有模块配置（配置只会写进已登录的 key），
  所以也不需要改写任何东西。

``name`` 回填：有昵称用昵称，否则「账号 N」（N 从 1 起，纯展示，用户随时可改）。

本模块是幂等的：新库（列已是 ``account_id``）直接返回，不做任何事。
"""

from __future__ import annotations

import re
from typing import Any

from app.core.logger import logger

#: 账号 id 允许的字符：字母/数字/下划线/连字符，必须以字母或数字开头。
#: 它会被拼进 URL 路径与磁盘文件名，所以绝不能含 `/`、`.`、空格等字符。
ACCOUNT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")

#: 保留 id：这些会与「全局实例」(None) 或内部约定撞车，禁止用户占用
RESERVED_ACCOUNT_IDS = frozenset({"none", "null", "global", "__global__", "_global"})

#: 展示名长度上限（纯展示，放宽一些）
ACCOUNT_NAME_MAX = 32


def normalize_account_id(raw: Any) -> str:
    """规范化并校验账号 id；非法返回空串（由调用方决定报错还是回退）。"""
    value = str(raw or "").strip()
    if not ACCOUNT_ID_RE.match(value):
        return ""
    if value.lower() in RESERVED_ACCOUNT_IDS:
        return ""
    return value


def suggest_account_id(existing: set[str] | list[str]) -> str:
    """给新账号生成一个没被占用的 ``botN``（仅作前端默认值）。"""
    taken = {str(x) for x in existing}
    n = 1
    while f"bot{n}" in taken:
        n += 1
    return f"bot{n}"


def normalize_account_name(raw: Any, fallback: str = "") -> str:
    """规范化展示名：去首尾空白、压掉换行、限长；空则用 fallback。"""
    value = " ".join(str(raw or "").split())
    if len(value) > ACCOUNT_NAME_MAX:
        value = value[:ACCOUNT_NAME_MAX]
    return value or fallback


def _digits_only(value: Any) -> bool:
    return bool(value) and str(value).strip().isdigit()


async def migrate_account_identity(db) -> None:
    """把旧库的 ``bots.bot_index`` 身份改成 ``bots.account_id``（幂等）。"""
    cols = await db.table_columns("bots")
    if not cols or "account_id" in cols:
        return

    old_bots = await db.fetchall("SELECT * FROM bots ORDER BY bot_index")
    try:
        old_accounts = await db.fetchall("SELECT * FROM bot_accounts")
    except Exception:  # 极老的库可能还没有这张表
        old_accounts = []
    accounts_by_index = {int(r["bot_index"]): r for r in old_accounts}

    used: set[str] = set()
    rows: list[tuple[str, str, dict]] = []
    for seq, bot in enumerate(old_bots):
        idx = int(bot.get("bot_index", seq))
        snap = accounts_by_index.get(idx) or {}
        qq = str(snap.get("user_id") or "").strip()
        nickname = str(snap.get("nickname") or "").strip()

        legacy_id = f"bot{seq + 1}"
        account_id = ""
        if _digits_only(qq) and qq not in used:
            account_id = qq
        if not account_id:
            account_id = legacy_id
            while account_id in used:
                account_id = f"{legacy_id}_{len(used)}"
        used.add(account_id)
        name = normalize_account_name(nickname, f"账号 {seq + 1}")
        rows.append((account_id, name, bot))

    # QQ 号 → account_id（只有"登录过、且 id 就是 QQ"的账号才有映射）
    qq_to_id = {aid: aid for aid, _name, bot in rows if _digits_only(aid)}

    await db.execute("ALTER TABLE bots RENAME TO bots_legacy")
    await db.execute("ALTER TABLE bot_accounts RENAME TO bot_accounts_legacy")
    await db.execute(
        """
        CREATE TABLE bots (
            account_id   TEXT PRIMARY KEY,
            name         TEXT NOT NULL DEFAULT '',
            ws_url       TEXT NOT NULL DEFAULT '',
            owner_id     TEXT,
            auto_connect INTEGER NOT NULL DEFAULT 0,
            conn_mode    TEXT NOT NULL DEFAULT 'forward',
            -- 迁移来的都是「老账号」：模块保持默认开（升级零行为漂移）。
            modules_default_enabled INTEGER NOT NULL DEFAULT 1,
            sort_order   INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE bot_accounts (
            account_id    TEXT PRIMARY KEY,
            user_id       TEXT,
            nickname      TEXT NOT NULL DEFAULT '',
            last_login_at INTEGER NOT NULL DEFAULT 0
        )
        """
    )

    for order, (account_id, name, bot) in enumerate(rows):
        await db.execute(
            "INSERT INTO bots (account_id, name, ws_url, owner_id, auto_connect, conn_mode, sort_order) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                account_id,
                name,
                bot.get("ws_url") or "",
                bot.get("owner_id"),
                1 if bot.get("auto_connect") else 0,
                bot.get("conn_mode") or "forward",
                order,
            ),
        )
        snap = accounts_by_index.get(int(bot.get("bot_index", order))) or {}
        # 快照按 account_id 重新挂载：即使 id 是 botN，也保留"上次登录的 QQ"
        await db.execute(
            "INSERT INTO bot_accounts (account_id, user_id, nickname, last_login_at) VALUES (?,?,?,?)",
            (
                account_id,
                snap.get("user_id"),
                snap.get("nickname") or "",
                int(snap.get("last_login_at") or 0),
            ),
        )

    await db.execute("DROP TABLE bots_legacy")
    await db.execute("DROP TABLE bot_accounts_legacy")

    # 模块配置/权限：key 是 QQ 号但 id 不是它时改写（通常无需改，因为 id 就是 QQ）
    rewritten = 0
    for table in ("module_config", "module_authority"):
        try:
            keys = await db.fetchall(f"SELECT DISTINCT bot_id FROM {table} WHERE bot_id IS NOT NULL")
        except Exception as e:  # noqa: BLE001 —— 极老的库可能缺这张表，跳过但必须留痕
            logger.warning(f"[DB] 迁移时读取 {table} 失败，该表 key 未改写（不影响账号本身）: {e}")
            continue
        for row in keys:
            old_key = str(row["bot_id"])
            new_key = qq_to_id.get(old_key)
            if new_key and new_key != old_key:
                await db.execute(
                    f"UPDATE {table} SET bot_id = ? WHERE bot_id = ?", (new_key, old_key)
                )
                rewritten += 1

    logger.info(
        f"[DB] 账号身份迁移完成：{len(rows)} 个账号改用 account_id"
        + (f"，改写 {rewritten} 条模块配置 key" if rewritten else "")
    )
