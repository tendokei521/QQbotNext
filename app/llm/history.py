"""
本地历史存储模块（框架级）。

每个对话线程（Conversation）以独立 JSON 文件持久化，文件名 history_{task_id}.json。
会话保存 = 保存其下所有对话。数据目录 P1 暂为 llm_chat_v2 模块目录（保证既有数据可读）。
"""

import json
import os
import re
import time
from typing import Dict, List, Optional

from app.llm import logger, llm_data_dir

# task_id 由 uuid4().hex[:12] 生成，仅允许十六进制（防路径穿越）
_TASK_ID_RE = re.compile(r"^[0-9a-f]{8,32}$")


class HistoryManager:
    """本地历史管理器 - 按 bot_id 独立。"""

    def __init__(self, bot_id: str):
        self.bot_id = bot_id
        self.history_dir = os.path.join(llm_data_dir(), "history")
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
    def load_history(self, task_id: str) -> Optional[dict]:
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

    def find_all_by_session(self, session_id: str) -> List[dict]:
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
    def list_tasks(self, session_id: Optional[str] = None) -> List[dict]:
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

    def export_text(self, task_id: str) -> Optional[str]:
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
                sender = msg.get("user_id", "用户")
                lines.append(f"[{sender}]: {content}")
            elif role == "assistant":
                lines.append(f"[助手]: {content}")
            elif role == "system":
                lines.append(f"[系统]: {content}")
            else:
                lines.append(f"[{role}]: {content}")
        return "\n".join(lines)

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
