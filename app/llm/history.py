"""
本地历史存储模块（框架级）。

每个对话线程（Conversation）以独立 JSON 文件持久化，文件名 history_{task_id}.json。
会话保存 = 保存其下所有对话。数据目录 P1 暂为 llm_chat_v2 模块目录（保证既有数据可读）。
"""

import json
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from app.llm import logger, llm_data_dir, safe_bot_id

# task_id 由 uuid4().hex[:12] 生成，仅允许十六进制（防路径穿越）
_TASK_ID_RE = re.compile(r"^[0-9a-f]{8,32}$")


class HistoryManager:
    """本地历史管理器 - 按 bot_id 独立（磁盘按 <bot_id>/history 隔离）。"""

    def __init__(self, bot_id: str):
        self.bot_id = bot_id
        # 每个账号独立目录：data/llm/<bot_id>/history
        self.history_dir = os.path.join(llm_data_dir(), safe_bot_id(bot_id), "history")
        os.makedirs(self.history_dir, exist_ok=True)

    def _file_path(self, task_id: str) -> str:
        if not task_id or not _TASK_ID_RE.match(str(task_id)):
            raise ValueError(f"非法 task_id: {task_id}")
        return os.path.join(self.history_dir, f"history_{task_id}.json")

    # ── 保存 ──────────────────────────────────────────────
    def save_session(self, session):
        """保存会话的全部对话线程。活跃对话最后保存（时间戳最大），恢复时优先设为活跃。"""
        convs = list(session.conversations.values())
        ordered = [c for c in convs if c.id != session.active_id]
        ordered += [c for c in convs if c.id == session.active_id]
        base = time.time()
        for i, conv in enumerate(ordered):
            self.save_conversation(session, conv, saved_at=base + i)

    def save_conversation(self, session, conv, saved_at=None):
        data = {
            "session_id": session.id,
            "type": session.type,
            "conv_id": conv.id,
            "task_id": conv.task_id,
            "title": conv.title,
            "bot_id": self.bot_id,
            "saved_at": int(saved_at) if saved_at is not None else int(time.time()),
            "messages": conv.data.history.copy(),
        }
        try:
            with open(self._file_path(conv.task_id), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.add_info(f"#{self.bot_id}").debug(
                f"历史已保存: {session.id} / {conv.title} (task: {conv.task_id}, {len(data['messages'])} 条)"
            )
        except Exception as e:
            logger.add_info(f"#{self.bot_id}").error(
                f"保存历史失败 (task: {conv.task_id}): {e}"
            )

    # ── 读取 ──────────────────────────────────────────────
    def load_history(self, task_id: str) -> dict | None:
        try:
            file_path = self._file_path(task_id)
        except ValueError:
            return None
        if not os.path.exists(file_path):
            logger.add_info(f"#{self.bot_id}").warning(f"历史文件不存在: {task_id}")
            return None
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.add_info(f"#{self.bot_id}").error(f"读取历史失败 (task: {task_id}): {e}")
            return None

    def find_all_by_session(self, session_id: str) -> list[dict]:
        """按会话 id 读取其全部对话归档（按 saved_at 升序）。"""
        result = []
        if not os.path.exists(self.history_dir):
            return result
        for filename in os.listdir(self.history_dir):
            if not filename.startswith("history_") or not filename.endswith(".json"):
                continue
            try:
                with open(os.path.join(self.history_dir, filename), "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("session_id") == session_id:
                    result.append(data)
            except Exception:
                continue
        result.sort(key=lambda d: d.get("saved_at", 0))
        return result

    # ── 列表 / 导出 / 删除 ────────────────────────────────
    def list_tasks(self, session_id: str | None = None) -> list[dict]:
        tasks = []
        if not os.path.exists(self.history_dir):
            return tasks
        for filename in os.listdir(self.history_dir):
            if not filename.startswith("history_") or not filename.endswith(".json"):
                continue
            try:
                with open(os.path.join(self.history_dir, filename), "r", encoding="utf-8") as f:
                    data = json.load(f)
                if session_id and data.get("session_id") != session_id:
                    continue
                tasks.append({
                    "task_id": data.get("task_id", filename[8:-5]),
                    "conv_id": data.get("conv_id", ""),
                    "session_id": data.get("session_id", "?"),
                    "title": data.get("title", ""),
                    "type": data.get("type", "?"),
                    "messages": len(data.get("messages", []) or []),
                    "saved_at": data.get("saved_at", 0),
                })
            except Exception:
                continue
        tasks.sort(key=lambda t: t.get("saved_at", 0), reverse=True)
        return tasks

    def export_text(self, task_id: str) -> str | None:
        data = self.load_history(task_id)
        if not data:
            return None
        lines = ["=== 对话历史导出 ==="]
        lines.append(f"会话: {data.get('session_id', '?')} / {data.get('title', '')}")
        lines.append(f"类型: {'群聊' if data.get('type') == 'group' else '私聊'}")
        lines.append(f"任务ID: {task_id}")
        lines.append("")
        for msg in data.get("messages", []) or []:
            role = msg.get("role", "?")
            content = msg.get("content", "")
            if role == "user":
                user_id = msg.get("user_id", "")
                nickname = msg.get("nickname", "") or user_id or "用户"
                sender = f"{nickname}({user_id})" if (nickname and user_id and nickname != str(user_id)) else nickname
                lines.append(f"[{sender}]: {content}")
            elif role == "assistant":
                lines.append(f"[助手]: {content}")
            elif role == "system":
                lines.append(f"[系统]: {content}")
            else:
                lines.append(f"[{role}]: {content}")
        return "\n".join(lines)

    def update_history(self, task_id: str, *, title: str | None = None, messages: list | None = None) -> dict | None:
        """更新归档里的标题或消息列表，返回更新后的完整数据。"""
        data = self.load_history(task_id)
        if data is None:
            return None
        if title is not None:
            data["title"] = str(title).strip()
        if messages is not None:
            if not isinstance(messages, list):
                raise ValueError("messages 必须是数组")
            data["messages"] = messages
        try:
            file_path = self._file_path(task_id)
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.add_info(f"#{self.bot_id}").error(f"更新历史失败 (task: {task_id}): {e}")
            raise
        logger.add_info(f"#{self.bot_id}").debug(
            f"历史已更新: {task_id} (title={data.get('title')!r}, messages={len(data.get('messages') or [])})"
        )
        return data

    def import_conversation(self, data: dict) -> dict:
        """导入/恢复一条完整对话记录。"""
        conv = dict(data or {})
        task_id = str(conv.get("task_id", "") or "")
        if not task_id or not _TASK_ID_RE.match(task_id):
            raise ValueError(f"非法 task_id: {task_id}")
        conv["task_id"] = task_id
        conv.setdefault("bot_id", self.bot_id)
        conv.setdefault("saved_at", int(time.time()))
        conv.setdefault("messages", [])
        file_path = self._file_path(task_id)
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(conv, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.add_info(f"#{self.bot_id}").error(f"导入历史失败 (task: {task_id}): {e}")
            raise
        logger.add_info(f"#{self.bot_id}").debug(f"历史已导入: {task_id} (messages={len(conv.get('messages') or [])})")
        return conv

    def delete_history(self, task_id: str) -> bool:
        try:
            file_path = self._file_path(task_id)
        except ValueError:
            return False
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.add_info(f"#{self.bot_id}").info(f"历史已删除: {task_id}")
            return True
        return False


class SQLiteHistoryStore:
    """SQLite 历史存储：按 bot_id 单文件，全索引查询，替代 JSON 文件目录扫描。

    接口与 HistoryManager 保持兼容，便于无痛切换。
    旧 JSON 仍会作为迁移源在首次启动时导入。
    """

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS conversations (
        bot_id     TEXT NOT NULL,
        task_id    TEXT NOT NULL,
        conv_id    TEXT NOT NULL,
        session_id TEXT NOT NULL,
        type       TEXT NOT NULL DEFAULT '',
        title      TEXT NOT NULL DEFAULT '',
        saved_at   INTEGER NOT NULL,
        messages   TEXT NOT NULL DEFAULT '[]',
        PRIMARY KEY (bot_id, task_id)
    );
    CREATE INDEX IF NOT EXISTS idx_conv_session ON conversations(bot_id, session_id);
    CREATE INDEX IF NOT EXISTS idx_conv_conv_id ON conversations(bot_id, conv_id);
    """

    def __init__(self, bot_id: Any, db_path: str | None = None) -> None:
        self.bot_id = safe_bot_id(bot_id)
        if db_path is None:
            db_path = os.path.join(llm_data_dir(), self.bot_id, "history", "history.db")
        self.db_path = str(db_path)
        self._history_dir = os.path.dirname(self.db_path)
        self._lock = threading.RLock()
        self._reopen()

    def _reopen(self) -> None:
        """（重新）打开 SQLite 连接并确保 schema。"""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(self._SCHEMA)
            self._conn.commit()
        self._migrate_legacy()

    @property
    def history_dir(self) -> str:
        return self._history_dir

    @history_dir.setter
    def history_dir(self, value: str) -> None:
        """兼容旧 JSON HistoryManager：外部重定向存储目录时，改用共享 history.db。"""
        new_dir = str(value)
        if os.path.abspath(new_dir) == os.path.abspath(self._history_dir) and self._conn is not None:
            return
        old_conn = self._conn
        self._conn = None
        try:
            if old_conn is not None:
                old_conn.close()
        except Exception:
            pass
        self._history_dir = new_dir
        old_db_path = self.db_path
        # 测试/自定义目录共享时不带 bot_id，保证不同 bot_id 可读取同一归档
        if os.path.dirname(old_db_path) != os.path.abspath(self._history_dir) and os.path.dirname(old_db_path) != self._history_dir:
            self.db_path = os.path.join(new_dir, "history.db")
        self._reopen()

    # ── 基础数据行 ──────────────────────────────────────

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        data = dict(row)
        try:
            data["messages"] = json.loads(data.get("messages") or "[]")
        except Exception:
            data["messages"] = []
        return data

    def _insert_conversation(self, data: dict) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO conversations
                    (bot_id, task_id, conv_id, session_id, type, title, saved_at, messages)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    self.bot_id,
                    str(data.get("task_id", "")),
                    str(data.get("conv_id", data.get("task_id", ""))),
                    str(data.get("session_id", "")),
                    str(data.get("type", "")),
                    str(data.get("title", "")),
                    int(data.get("saved_at", int(time.time()))),
                    json.dumps(data.get("messages", []), ensure_ascii=False),
                ),
            )
            self._conn.commit()

    def _migrate_legacy(self) -> None:
        """把旧 JSON 历史文件导入 SQLite（幂等）。"""
        legacy_dir = os.path.join(llm_data_dir(), self.bot_id, "history")
        if not os.path.isdir(legacy_dir):
            return
        with self._lock:
            count = self._conn.execute(
                "SELECT COUNT(*) AS c FROM conversations WHERE bot_id=?", (self.bot_id,)
            ).fetchone()["c"]
        if count:
            return
        imported = 0
        for name in os.listdir(legacy_dir):
            if not name.startswith("history_") or not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(legacy_dir, name), "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("bot_id") != self.bot_id:
                    # 旧文件可能没有 bot_id 或 root legacy 数据；仍尝试按当前 bot 导入
                    data["bot_id"] = self.bot_id
                self._insert_conversation(data)
                imported += 1
            except Exception as e:
                logger.add_info(f"#{self.bot_id}").warning(
                    f"[History] 旧 JSON 迁移失败 {name}: {e}"
                )
        if imported:
            logger.add_info(f"#{self.bot_id}").info(
                f"[History] 已从旧 JSON 导入 {imported} 个对话到 SQLite"
            )

    # ── 保存 ──────────────────────────────────────────────

    def save_session(self, session) -> None:
        convs = list(session.conversations.values())
        ordered = [c for c in convs if c.id != session.active_id]
        ordered += [c for c in convs if c.id == session.active_id]
        base = time.time()
        for i, conv in enumerate(ordered):
            self.save_conversation(session, conv, saved_at=base + i)

    def save_conversation(self, session, conv, saved_at=None) -> None:
        data = {
            "session_id": session.id,
            "type": session.type,
            "conv_id": conv.id,
            "task_id": conv.task_id,
            "title": conv.title,
            "bot_id": self.bot_id,
            "saved_at": int(saved_at) if saved_at is not None else int(time.time()),
            "messages": conv.data.history.copy(),
        }
        self._insert_conversation(data)
        logger.add_info(f"#{self.bot_id}").debug(
            f"历史已保存(SQLite): {session.id} / {conv.title} "
            f"(task: {conv.task_id}, {len(data['messages'])} 条)"
        )

    # ── 读取 ──────────────────────────────────────────────

    def load_history(self, task_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM conversations WHERE bot_id=? AND task_id=?",
                (self.bot_id, str(task_id)),
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def find_all_by_session(self, session_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM conversations WHERE bot_id=? AND session_id=? ORDER BY saved_at ASC",
                (self.bot_id, str(session_id)),
            ).fetchall()
            # 兼容旧 JSON 共享目录：当自定义 history_dir 且没有本 bot 数据时，
            # 允许按 session_id 读取同一目录下的归档（测试/多 bot 共享归档场景）。
            if not rows and self._is_shared_custom_dir():
                rows = self._conn.execute(
                    "SELECT * FROM conversations WHERE session_id=? ORDER BY saved_at ASC",
                    (str(session_id),),
                ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def _is_shared_custom_dir(self) -> bool:
        default_dir = os.path.join(llm_data_dir(), self.bot_id, "history")
        return os.path.abspath(self._history_dir) != os.path.abspath(default_dir)

    def list_tasks(self, session_id: str | None = None) -> list[dict]:
        with self._lock:
            if session_id:
                rows = self._conn.execute(
                    "SELECT * FROM conversations WHERE bot_id=? AND session_id=? ORDER BY saved_at DESC",
                    (self.bot_id, str(session_id)),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM conversations WHERE bot_id=? ORDER BY saved_at DESC",
                    (self.bot_id,),
                ).fetchall()
        tasks = []
        for row in rows:
            data = self._row_to_dict(row)
            tasks.append({
                "task_id": data.get("task_id", ""),
                "conv_id": data.get("conv_id", ""),
                "session_id": data.get("session_id", "?"),
                "title": data.get("title", ""),
                "type": data.get("type", "?"),
                "messages": len(data.get("messages", []) or []),
                "saved_at": data.get("saved_at", 0),
            })
        return tasks

    def export_text(self, task_id: str) -> str | None:
        data = self.load_history(task_id)
        if not data:
            return None
        lines = ["=== 对话历史导出 ==="]
        lines.append(f"会话: {data.get('session_id', '?')} / {data.get('title', '')}")
        lines.append(f"类型: {'群聊' if data.get('type') == 'group' else '私聊'}")
        lines.append(f"任务ID: {task_id}")
        lines.append("")
        for msg in data.get("messages", []) or []:
            role = msg.get("role", "?")
            content = msg.get("content", "")
            if role == "user":
                user_id = msg.get("user_id", "")
                nickname = msg.get("nickname", "") or user_id or "用户"
                sender = f"{nickname}({user_id})" if (nickname and user_id and nickname != str(user_id)) else nickname
                lines.append(f"[{sender}]: {content}")
            elif role == "assistant":
                lines.append(f"[助手]: {content}")
            elif role == "system":
                lines.append(f"[系统]: {content}")
            else:
                lines.append(f"[{role}]: {content}")
        return "\n".join(lines)

    def update_history(self, task_id: str, *, title: str | None = None, messages: list | None = None) -> dict | None:
        """更新归档里的标题或消息列表，返回更新后的完整数据。"""
        data = self.load_history(task_id)
        if data is None:
            return None
        if title is not None:
            data["title"] = str(title).strip()
        if messages is not None:
            if not isinstance(messages, list):
                raise ValueError("messages 必须是数组")
            data["messages"] = messages
        self._insert_conversation(data)
        logger.add_info(f"#{self.bot_id}").debug(
            f"历史已更新(SQLite): {task_id} (title={data.get('title')!r}, messages={len(data.get('messages') or [])})"
        )
        return data

    def import_conversation(self, data: dict) -> dict:
        """导入/恢复一条完整对话记录。"""
        conv = dict(data or {})
        task_id = str(conv.get("task_id", "") or "")
        if not task_id or not _TASK_ID_RE.match(task_id):
            raise ValueError(f"非法 task_id: {task_id}")
        conv["task_id"] = task_id
        conv.setdefault("bot_id", self.bot_id)
        conv.setdefault("saved_at", int(time.time()))
        conv.setdefault("messages", [])
        self._insert_conversation(conv)
        logger.add_info(f"#{self.bot_id}").debug(
            f"历史已导入(SQLite): {task_id} (messages={len(conv.get('messages') or [])})"
        )
        return conv

    def delete_history(self, task_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM conversations WHERE bot_id=? AND task_id=?",
                (self.bot_id, str(task_id)),
            )
            self._conn.commit()
            deleted = cur.rowcount > 0
        if deleted:
            logger.add_info(f"#{self.bot_id}").info(f"历史已删除(SQLite): {task_id}")
        return deleted

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass
