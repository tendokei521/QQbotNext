"""会话历史服务：为 WebUI 提供会话列表 / 对话线程 / 编辑 / 导出。

面板使用独立的 HistoryStore 访问本地归档；遇到仍在内存中的活跃会话时，
会同步修改 SessionManager 里的 Conversation，避免 UI 修改被下一次保存覆盖。
"""

from __future__ import annotations

from typing import Any

from app.llm.session import SessionManager


class SessionHistoryService:
    """按 bot_id 提供本地会话历史管理。"""

    def __init__(self, bot_id: Any) -> None:
        self.bot_id = str(bot_id)
        self.session_mgr = SessionManager(self.bot_id)
        self.history = self.session_mgr.history

    # ---------- 查询 ----------

    def list_sessions(self) -> list[dict]:
        """把归档 conversation 聚合为会话维度列表。"""
        tasks = self.history.list_tasks()
        sessions: dict[str, dict] = {}

        for task in tasks:
            sid = str(task.get("session_id", "") or "?")
            if not sid or sid == "?":
                continue
            entry = sessions.setdefault(sid, {
                "session_id": sid,
                "type": task.get("type", ""),
                "conversation_count": 0,
                "total_messages": 0,
                "last_saved_at": 0,
            })
            entry["conversation_count"] += 1
            entry["total_messages"] += int(task.get("messages", 0) or 0)
            entry["last_saved_at"] = max(entry["last_saved_at"], int(task.get("saved_at", 0) or 0))
            if not entry["type"]:
                entry["type"] = task.get("type", "")

        return sorted(sessions.values(), key=lambda s: s["last_saved_at"], reverse=True)

    def get_session(self, session_id: str) -> dict | None:
        """返回某个会话及其全部对话线程。"""
        tasks = self.history.list_tasks(session_id)
        if not tasks:
            return None
        conversations = [{
            "task_id": str(t.get("task_id", "")),
            "conv_id": str(t.get("conv_id", "") or t.get("task_id", "")),
            "title": str(t.get("title", "") or ""),
            "messages": int(t.get("messages", 0) or 0),
            "saved_at": int(t.get("saved_at", 0) or 0),
        } for t in tasks]
        return {
            "session_id": session_id,
            "type": str(tasks[0].get("type", "") or ""),
            "conversations": conversations,
        }

    def get_conversation(self, task_id: str) -> dict | None:
        """返回完整对话归档（含 messages 数组）。"""
        return self.history.load_history(task_id)

    # ---------- 编辑 ----------

    def rename_conversation(self, session_id: str, task_id: str, title: str) -> dict:
        title = str(title or "").strip()
        if not title:
            raise ValueError("标题不能为空")
        data = self.history.update_history(task_id, title=title)
        if data is None:
            raise ValueError("对话不存在")
        self._sync_active_meta(session_id, task_id, title=title)
        return data

    def delete_message(self, session_id: str, task_id: str, index: int) -> dict:
        data = self.history.load_history(task_id)
        if data is None:
            raise ValueError("对话不存在")
        messages = data.get("messages", []) or []
        try:
            index = int(index)
        except (TypeError, ValueError):
            raise ValueError("消息索引不合法")
        if index < 0 or index >= len(messages):
            raise ValueError(f"消息索引越界: {index}")

        new_messages = [m for i, m in enumerate(messages) if i != index]
        data = self.history.update_history(task_id, messages=new_messages)
        if data is None:
            raise ValueError("对话不存在")
        self._sync_active_messages(session_id, task_id, new_messages)
        return data

    def edit_message(self, session_id: str, task_id: str, index: int, content: str) -> dict:
        content = str(content or "")
        data = self.history.load_history(task_id)
        if data is None:
            raise ValueError("对话不存在")
        messages = data.get("messages", []) or []
        try:
            index = int(index)
        except (TypeError, ValueError):
            raise ValueError("消息索引不合法")
        if index < 0 or index >= len(messages):
            raise ValueError(f"消息索引越界: {index}")
        messages = [dict(m) for m in messages]
        messages[index]["content"] = content
        data = self.history.update_history(task_id, messages=messages)
        if data is None:
            raise ValueError("对话不存在")
        self._sync_active_messages(session_id, task_id, messages)
        return data

    def add_message(self, session_id: str, task_id: str, payload: dict) -> dict:
        data = self.history.load_history(task_id)
        if data is None:
            raise ValueError("对话不存在")
        role = str(payload.get("role", "") or "").strip()
        content = str(payload.get("content", "") or "").strip()
        if role not in ("user", "assistant", "system"):
            raise ValueError("role 必须是 user / assistant / system")
        if not content:
            raise ValueError("消息内容不能为空")
        message: dict = {"role": role, "content": content}
        for key in ("user_id", "nickname", "message_id", "time"):
            value = payload.get(key)
            if value not in (None, ""):
                message[key] = value
        messages = list(data.get("messages", []) or [])
        messages.append(message)
        data = self.history.update_history(task_id, messages=messages)
        if data is None:
            raise ValueError("对话不存在")
        self._sync_active_messages(session_id, task_id, messages)
        return data

    def delete_conversation(self, session_id: str, task_id: str) -> bool:
        """删除对话归档。

        若该对话是仍在内存中的活跃会话，仅当会话中还有其它对话时才允许删除，
        避免把活跃会话的唯一对话删掉后又被下一次保存重新写回。
        """
        data = self.history.load_history(task_id)
        if data is None:
            return False

        # 活跃会话同步
        session, conv = self._find_active_conversation(session_id, task_id)
        if session is not None and conv is not None:
            if len(session.conversations) <= 1:
                raise ValueError("当前活跃会话仅剩一个对话，不能直接删除；可清空消息或等待会话归档后删除")
            session.delete_conversation(conv.id)
            session.touch()
            self.history.save_session(session)

        return self.history.delete_history(task_id)

    def delete_session(self, session_id: str) -> int:
        """删除一个会话及其全部对话归档。"""
        tasks = self.history.list_tasks(session_id)
        if not tasks:
            return 0
        # 从内存中彻底移除，避免后续保存把归档写回
        self.session_mgr.forget_session(session_id)
        deleted = 0
        for task in tasks:
            if self.history.delete_history(str(task.get("task_id", ""))):
                deleted += 1
        return deleted

    def bulk_delete_sessions(self, session_ids: list[str]) -> dict:
        result = {"sessions": [], "conversations": 0, "failed": []}
        for sid in session_ids:
            try:
                count = self.delete_session(sid)
                result["sessions"].append(sid)
                result["conversations"] += count
            except Exception as e:
                result["failed"].append({"session_id": sid, "error": str(e)})
        return result

    def bulk_delete_conversations(self, session_id: str, task_ids: list[str]) -> dict:
        result = {"deleted": [], "failed": []}
        for tid in task_ids:
            try:
                if self.delete_conversation(session_id, tid):
                    result["deleted"].append(tid)
                else:
                    result["failed"].append({"task_id": tid, "error": "对话不存在"})
            except Exception as e:
                result["failed"].append({"task_id": tid, "error": str(e)})
        return result

    # ---------- 导出 / 备份 ----------

    def export_text(self, task_id: str) -> str | None:
        return self.history.export_text(task_id)

    def export_json(self, task_id: str) -> dict | None:
        return self.history.load_history(task_id)

    def export_session_json(self, session_id: str) -> dict | None:
        tasks = self.history.list_tasks(session_id)
        if not tasks:
            return None
        conversations = []
        for task in tasks:
            data = self.history.load_history(str(task.get("task_id", "")))
            if data:
                conversations.append(data)
        return {
            "session_id": session_id,
            "type": str(tasks[0].get("type", "") or ""),
            "conversations": conversations,
        }

    def restore_session(self, data: dict) -> dict:
        """从备份对象恢复会话。

        支持两种输入：
        - 会话备份：``{"session_id": "...", "type": "...", "conversations": [...]}``
        - 单对话备份：``{"task_id": "...", "session_id": "...", "messages": [...]}``
        """
        if not isinstance(data, dict):
            raise ValueError("备份数据必须是一个对象")

        conversations = data.get("conversations")
        if isinstance(conversations, list):
            session_id = str(data.get("session_id", "") or "").strip()
            if not session_id:
                raise ValueError("备份缺少 session_id")
            session_type = str(data.get("type", "") or "").strip() or "group"
            restored = []
            for raw in conversations or []:
                if not isinstance(raw, dict):
                    continue
                conv = dict(raw)
                conv["session_id"] = session_id
                conv["type"] = session_type
                conv["bot_id"] = self.bot_id
                restored.append(self.history.import_conversation(conv))
            return {"session_id": session_id, "type": session_type, "restored": len(restored)}

        task_id = str(data.get("task_id", "") or "").strip()
        if not task_id:
            raise ValueError("备份缺少 task_id 或 conversations")
        conv = dict(data)
        conv.setdefault("session_id", str(data.get("session_id", "") or "unknown"))
        conv.setdefault("type", str(data.get("type", "") or "group"))
        conv.setdefault("bot_id", self.bot_id)
        restored = self.history.import_conversation(conv)
        return {
            "session_id": str(conv.get("session_id", "")),
            "type": str(conv.get("type", "")),
            "restored": 1,
            "conversation": restored,
        }

    # ---------- 活跃会话同步 ----------

    def _find_active_conversation(self, session_id: str, task_id: str):
        session = self.session_mgr.get_session(session_id)
        if session is None:
            return None, None
        for conv in session.conversations.values():
            if str(conv.task_id) == str(task_id):
                return session, conv
        return None, None

    def _sync_active_meta(self, session_id: str, task_id: str, title: str) -> None:
        session, conv = self._find_active_conversation(session_id, task_id)
        if session is None or conv is None:
            return
        conv.title = title
        session.touch()
        self.history.save_session(session)

    def _sync_active_messages(self, session_id: str, task_id: str, messages: list) -> None:
        session, conv = self._find_active_conversation(session_id, task_id)
        if session is None or conv is None:
            return
        conv.data.history = list(messages or [])
        session.touch()
        self.history.save_session(session)
