"""把存量会话历史迁移成**事件日志**（P4 第 4 步）。

## 为什么需要

P4 之前，会话历史只有 `conversations.messages` 这一整包 JSON 数组；P4 之后事件日志
（`history_events`）是唯一真相，`messages` 只是它的派生缓存。新写入的轮次会自动双写，
但**存量数据没有事件**——不迁移的话：

- `derive_messages()` 对老会话返回空（在线不变式一开就会报"历史凭空消失"）；
- WebUI 按 id 编辑/删除对老消息无从折叠（它们没有 `entry_id`）。

## 做法

对每个 `data/llm/<safe_bot_id>/history/history.db`：

1. 读出 `conversations` 的每一行，把 `messages` 数组按下标生成事件：
   `user` → `user/message`、`assistant` → `assistant/message`；`seq = index + 1`，
   `entry_id = "<task_id>:idx:<index>"`（与 `_entry_id_of` 的兜底规则**逐字一致**，
   否则 WebUI 的按 id 折叠会失配）；
2. **幂等**：该 task 已有事件行就整条跳过（重复跑不会产生重复事件）；
3. `--dry-run` 只打印计划，不写库；真正写入前**自动备份** `history.db`；
4. 统计口径写清楚：扫了几个库、迁移/跳过/失败各多少 task、各多少条消息。

`system` 角色等非 surface-eligible 的消息**不生成事件**（它们仍在缓存里、仍会进装配），
与 `session_log.SURFACE_ELIGIBLE` 保持一致。

## 用法

```bash
python scripts/migrate_session_events.py --dry-run     # 先看计划
python scripts/migrate_session_events.py               # 真跑（自动备份）
python scripts/migrate_session_events.py --db <单个 history.db>   # 只处理一个库
```
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROLE_EVENTS = {"user": "user/message", "assistant": "assistant/message"}

_CREATE_EVENTS = """
CREATE TABLE IF NOT EXISTS history_events (
    bot_id  TEXT NOT NULL,
    task_id TEXT NOT NULL,
    seq     INTEGER NOT NULL,
    type    TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    ts      INTEGER NOT NULL,
    PRIMARY KEY (bot_id, task_id, seq)
);
"""


def find_dbs(data_dir: Path) -> list[Path]:
    """所有 ``<bot>/history/history.db``（按路径排序，便于复现）。"""
    return sorted(data_dir.glob("*/history/history.db"))


def _events_for(task_id: str, messages: list, bot_id: str, ts: int) -> list[dict]:
    """把一条会话的消息数组翻译成事件行（不含 seq，由写入方分配）。"""
    events: list[dict] = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        type_ = ROLE_EVENTS.get(str(message.get("role") or ""))
        if type_ is None:
            continue   # system / 未知角色：留在缓存里，不进事件（与投影规则一致）
        events.append({
            "bot_id": bot_id,
            "task_id": task_id,
            "seq": index + 1,
            "type": type_,
            "payload": json.dumps(
                {"entry": message, "entry_id": f"{task_id}:idx:{index}"},
                ensure_ascii=False,
            ),
            "ts": ts,
        })
    return events


def migrate_db(db: Path, *, dry_run: bool = False) -> dict:
    """迁移一个库，返回统计（``tasks``/``migrated``/``skipped``/``messages``）。"""
    stats = {"db": str(db), "tasks": 0, "migrated": 0, "skipped": 0, "messages": 0, "backup": ""}
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_CREATE_EVENTS)
        rows = conn.execute("SELECT bot_id, task_id, messages FROM conversations").fetchall()
        stats["tasks"] = len(rows)
        plan: list[dict] = []
        for row in rows:
            task_id = str(row["task_id"] or "")
            bot_id = str(row["bot_id"] or "")
            existing = conn.execute(
                "SELECT COUNT(1) FROM history_events WHERE bot_id=? AND task_id=?",
                (bot_id, task_id),
            ).fetchone()[0]
            if int(existing or 0) > 0:
                stats["skipped"] += 1
                continue
            try:
                messages = json.loads(row["messages"] or "[]")
            except Exception:
                messages = []
            if not isinstance(messages, list) or not messages:
                stats["skipped"] += 1
                continue
            events = _events_for(task_id, messages, bot_id, int(time.time()))
            if not events:
                stats["skipped"] += 1
                continue
            plan.extend(events)
            stats["migrated"] += 1
            stats["messages"] += len(events)

        if dry_run or not plan:
            return stats

        # 真写之前先备份：迁移是"只加行"，但库里的事故从来不是只有一条路径
        backup = db.with_suffix(f".db.bak.{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(db, backup)
        stats["backup"] = str(backup)

        with conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO history_events (bot_id, task_id, seq, type, payload, ts)
                VALUES (:bot_id, :task_id, :seq, :type, :payload, :ts)
                """,
                plan,
            )
        return stats
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把存量会话历史迁移成事件日志（幂等）")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不写库")
    parser.add_argument("--data-dir", default="", help="覆盖 data/llm 目录")
    parser.add_argument("--db", default="", help="只处理指定的 history.db")
    args = parser.parse_args(argv)

    if args.db:
        dbs = [Path(args.db)]
    else:
        from app.agent import llm_data_dir

        data_dir = Path(args.data_dir) if args.data_dir else Path(llm_data_dir())
        dbs = find_dbs(data_dir)
    if not dbs:
        print("[skip] 没找到任何 history.db（数据目录不存在或还没有历史）")
        return 0

    total = {"tasks": 0, "migrated": 0, "skipped": 0, "messages": 0}
    failures: list[str] = []
    for db in dbs:
        if not db.is_file():
            failures.append(f"{db}: 文件不存在")
            continue
        try:
            stats = migrate_db(db, dry_run=args.dry_run)
        except Exception as e:  # noqa: BLE001 —— 单个库失败不该中断整体迁移
            failures.append(f"{db}: {e}")
            continue
        for key in total:
            total[key] += stats[key]
        suffix = "（dry-run）" if args.dry_run else ""
        print(
            f"[ok] {db}{suffix}: task {stats['migrated']} 迁移 / {stats['skipped']} 跳过，"
            f"事件 {stats['messages']} 条"
            + (f"，备份 {stats['backup']}" if stats["backup"] else "")
        )

    print(
        f"\n合计：{len(dbs)} 个库，task {total['migrated']} 迁移 / {total['skipped']} 跳过，"
        f"事件 {total['messages']} 条" + ("（dry-run，未写库）" if args.dry_run else "")
    )
    for failure in failures:
        print(f"[fail] {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
