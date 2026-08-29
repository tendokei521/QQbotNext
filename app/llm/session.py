"""会话管理模块（框架级，化用 AstrBot「会话/对话分离」）。

- Session：对话窗口（group_xxx / private_xxx），是参与者、冷却、超时的载体；
- Conversation：窗口内的对话线程，拥有独立的 history / tags / task_id，
  支持切换、删除；
- Session.data / task_id 为「当前活跃对话」的代理，保证旧调用不破坏。
"""

import re
import time
import threading
import uuid
from typing import Any

from app.llm import logger
from app.llm.history import SQLiteHistoryStore

# 防御：模型/中转偶发的孤立 "rate." 不应作为有效助手回复回灌上下文
_JUNK_ASSISTANT_RE = re.compile(r"^\s*rate\.\s*$", re.IGNORECASE)


def _is_junk_assistant(content) -> bool:
    return bool(content and _JUNK_ASSISTANT_RE.match(str(content)))


class ConversationData:
    def __init__(self):
        self.history: list = []


class Conversation:
    """一个对话线程：独立历史、标签、task_id。"""

    def __init__(self, title: str = "", conv_id: str | None = None):
        self.id = conv_id or uuid.uuid4().hex[:12]
        self.title = title or f"对话 {self.id[:6]}"
        self.task_id = uuid.uuid4().hex[:12]
        self.data = ConversationData()
        self.created_at = int(time.time())
        self.updated_at = int(time.time())


class SessionConfig:
    create_time: int = 0
    last_time: int = 0
    timeout: int = 60
    count_history: int = 10

    def __init__(self):
        self.create_time = 0
        self.last_time = 0
        self.timeout = 60
        self.count_history = 10


class Session:
    """对话窗口。内部包含多个 Conversation，data/task_id 指向当前活跃对话。"""

    def __init__(self, session_id: str, session_type: str, timeout: int = 60):
        self.id = session_id
        self.type = session_type
        self.config = SessionConfig()
        self.config.timeout = timeout
        self.config.create_time = int(time.time())
        self.config.last_time = self.config.create_time

        self.participants: set[str] = set()
        self.last_reply_time: float = 0
        self.reply_cooldown: int = 5

        self.conversations: dict[str, Conversation] = {}
        self.active_id: str | None = None
        self._new_conversation()

    # ── 对话操作 ──────────────────────────────────────────
    def _new_conversation(self, title: str = "") -> Conversation:
        conv = Conversation(title=title)
        self.conversations[conv.id] = conv
        self.active_id = conv.id
        return conv

    def new_conversation(self, title: str = "") -> Conversation:
        return self._new_conversation(title)

    def switch_conversation(self, conv_id: str) -> bool:
        if conv_id in self.conversations:
            self.active_id = conv_id
            self.touch()
            return True
        return False

    def delete_conversation(self, conv_id: str) -> bool:
        if conv_id not in self.conversations:
            return False
        if len(self.conversations) <= 1:
            return False  # 保留至少一个对话
        del self.conversations[conv_id]
        if self.active_id == conv_id:
            self.active_id = next(iter(self.conversations), None)
        return True

    def list_conversations(self) -> list[dict]:
        return [
            {
                "id": c.id,
                "title": c.title,
                "count": len(c.data.history),
                "updated_at": c.updated_at,
            }
            for c in self.conversations.values()
        ]

    # ── 活跃对话代理（兼容旧调用） ─────────────────────────
    @property
    def active(self) -> Conversation | None:
        return self.conversations.get(self.active_id)

    @property
    def data(self) -> ConversationData | None:
        conv = self.active
        return conv.data if conv else None

    @property
    def task_id(self) -> str:
        conv = self.active
        return conv.task_id if conv else ""

    @task_id.setter
    def task_id(self, value: str) -> None:
        if self.active:
            self.active.task_id = value

    # ── 会话生命周期 ───────────────────────────────────────
    def reset(self):
        self._new_conversation()

    def touch(self):
        self.config.last_time = int(time.time())
        if self.active:
            self.active.updated_at = int(time.time())

    def is_alive(self) -> bool:
        return (time.time() - self.config.last_time) < self.config.timeout

    def can_reply(self) -> bool:
        return (time.time() - self.last_reply_time) >= self.reply_cooldown

    def mark_replied(self):
        self.last_reply_time = time.time()

    def add_participant(self, user_id: str):
        self.participants.add(str(user_id))

    def has_participant(self, user_id: str) -> bool:
        return str(user_id) in self.participants

    def remove_participant(self, user_id: str):
        self.participants.discard(str(user_id))


class SessionManager:
    """按 bot_id 单例的会话管理器。"""

    _instances: dict = {}
    _lock = threading.Lock()

    def __new__(cls, bot_id: str):
        if bot_id not in cls._instances:
            with cls._lock:
                if bot_id not in cls._instances:
                    instance = super().__new__(cls)
                    instance._init(bot_id)
                    cls._instances[bot_id] = instance
        return cls._instances[bot_id]

    def _init(self, bot_id: str):
        self.bot_id = bot_id
        self.sessions: dict[str, Session] = {}
        self.lock = threading.RLock()
        self.history = SQLiteHistoryStore(bot_id)
        # 会话过期/归档时的可选回调（长期记忆归档蒸馏用）：callable(session)
        self.on_archive = None
        self._stop_event = threading.Event()
        self._cleanup_thread: threading.Thread | None = None
        self._start_auto_cleanup()

    def _notify_archive(self, session) -> None:
        cb = self.on_archive
        if cb is None:
            return
        try:
            cb(session)
        except Exception:
            pass

    def stop_cleanup(self):
        self._stop_event.set()
        if self._cleanup_thread and self._cleanup_thread.is_alive():
            self._cleanup_thread.join(timeout=3)
        self._cleanup_thread = None

    def restart_cleanup(self):
        """恢复清理线程（stop 后 / 线程意外退出时调用，幂等）。"""
        self._stop_event.clear()
        if not self._cleanup_thread or not self._cleanup_thread.is_alive():
            self._start_auto_cleanup()

    def close(self):
        """关闭历史存储连接并停止清理线程。"""
        self.stop_cleanup()
        try:
            close_fn = getattr(self.history, "close", None)
            if callable(close_fn):
                close_fn()
        except Exception:
            pass

    def _start_auto_cleanup(self):
        def cleanup_task():
            while not self._stop_event.is_set():
                self._stop_event.wait(30)
                if self._stop_event.is_set():
                    break
                try:
                    self._cleanup_expired()
                except Exception as e:
                    logger.add_info(f"#{self.bot_id}").warning(f"清理过期会话异常: {e}")

        self._cleanup_thread = threading.Thread(target=cleanup_task, daemon=True)
        self._cleanup_thread.start()

    def _cleanup_expired(self):
        with self.lock:
            expired = [sid for sid, s in self.sessions.items() if not s.is_alive()]
            for sid in expired:
                session = self.sessions.pop(sid)
                self.history.save_session(session)
                self._notify_archive(session)
                logger.add_info(f"#{self.bot_id}").info(
                    f"会话过期已归档: {sid}"
                )

    # ── 会话 CRUD ─────────────────────────────────────────
    def create_session(self, session_id: str, session_type: str, timeout: int = 60) -> Session:
        self.restart_cleanup()  # 懒恢复清理线程（bot 重连/框架重启后）
        session = Session(session_id, session_type, timeout)
        with self.lock:
            self.sessions[session_id] = session
        logger.add_info(f"#{self.bot_id}").info(f"创建会话: {session_id}")
        return session

    def get_session(self, session_id: str) -> Session | None:
        with self.lock:
            session = self.sessions.get(session_id)
            if session and session.is_alive():
                return session
            if session:
                del self.sessions[session_id]
                self.history.save_session(session)
                self._notify_archive(session)
            return None

    def destroy_session(self, session_id: str):
        with self.lock:
            session = self.sessions.pop(session_id, None)
            if session:
                self.history.save_session(session)
                self._notify_archive(session)
                logger.add_info(f"#{self.bot_id}").info(f"会话已结束: {session_id}")

    # ── 消息 ──────────────────────────────────────────────
    MAX_HISTORY_MESSAGES = 200  # 单对话内存/归档有界：超过后裁剪最旧消息

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        user_id: str = "",
        nickname: str = "",
        message_id: Any = None,
        timestamp: int | None = None,
    ):
        session = self.get_session(session_id)
        if session and session.data is not None:
            if role == "assistant" and _is_junk_assistant(content):
                return
            msg = {
                "role": role,
                "content": content,
                "time": int(time.time()) if timestamp is None else int(timestamp),
            }
            if user_id:
                msg["user_id"] = str(user_id)
            if nickname:
                msg["nickname"] = str(nickname)
            if message_id not in (None, ""):
                msg["message_id"] = str(message_id)
            history = session.data.history
            history.append(msg)
            if len(history) > self.MAX_HISTORY_MESSAGES:
                dropped = len(history) - self.MAX_HISTORY_MESSAGES
                del history[:dropped]
                logger.add_info(f"#{self.bot_id}").debug(
                    f"历史裁剪: {session_id} 丢弃最旧 {dropped} 条 (上限 {self.MAX_HISTORY_MESSAGES})"
                )
            session.touch()

    def get_history(self, session_id: str, limit: int = 10) -> list[dict]:
        session = self.get_session(session_id)
        if not session or session.data is None:
            return []
        # 过滤掉空 assistant 消息与已知杂散 token：模型返回空/rate. 不应再作为上下文回灌
        filtered = [
            m for m in session.data.history
            if not (
                m.get("role") == "assistant"
                and (not str(m.get("content") or "").strip() or _is_junk_assistant(m.get("content")))
            )
        ]
        result = []
        for m in filtered[-limit:]:
            item = {"role": m["role"], "content": m["content"]}
            for key in ("user_id", "nickname", "message_id", "time"):
                if m.get(key) is not None:
                    item[key] = m[key]
            result.append(item)
        return result

    # ── 多对话操作 ────────────────────────────────────────
    def new_conversation(self, session_id: str, title: str = "") -> dict | None:
        session = self.get_session(session_id)
        if not session:
            return None
        conv = session.new_conversation(title)
        self.history.save_session(session)
        return {"id": conv.id, "task_id": conv.task_id, "title": conv.title}

    def switch_conversation(self, session_id: str, conv_id: str) -> bool:
        session = self.get_session(session_id)
        if not session:
            return False
        return session.switch_conversation(conv_id)

    def list_conversations(self, session_id: str) -> list[dict]:
        session = self.get_session(session_id)
        if not session:
            return []
        return session.list_conversations()

    def delete_conversation(self, session_id: str, conv_id: str) -> bool:
        session = self.get_session(session_id)
        if not session:
            return False
        deleted_conv = session.conversations.get(conv_id)
        if not session.delete_conversation(conv_id):
            return False
        if deleted_conv:
            self.history.delete_history(deleted_conv.task_id)
        self.history.save_session(session)
        return True

    # ── 归档恢复 ──────────────────────────────────────────
    def restore_session_from_archive(self, session: Session, session_id: str):
        """从归档恢复会话的全部对话线程，最近的一个设为活跃。

        若会话刚创建且只有一个空对话，先清空再填充，避免空对话残留。
        """
        convs = self.history.find_all_by_session(session_id)
        if not convs:
            return
        # 新建会话自带 1 个空对话 → 清掉再填充
        fresh = len(session.conversations) == 1 and not any(c.data.history for c in session.conversations.values())
        if fresh:
            session.conversations.clear()
            session.active_id = None
        for data in convs:
            conv_id = data.get("conv_id") or data.get("task_id")
            conv = Conversation(title=data.get("title", ""), conv_id=conv_id)
            conv.task_id = data.get("task_id", conv.task_id)
            conv.data.history = [
                m for m in (data.get("messages", []) or [])
                if not (
                    m.get("role") == "assistant"
                    and (not str(m.get("content") or "").strip() or _is_junk_assistant(m.get("content")))
                )
            ]
            session.conversations[conv.id] = conv
        latest = max(convs, key=lambda d: d.get("saved_at", 0))
        session.active_id = latest.get("conv_id") or latest.get("task_id")
        logger.add_info(f"#{self.bot_id}").info(
            f"归档恢复: {session_id} ({len(session.conversations)} 个对话)"
        )

    def get_stats(self) -> dict:
        with self.lock:
            return {
                "active": len(self.sessions),
                "groups": sum(1 for s in self.sessions.values() if s.type == "group"),
                "privates": sum(1 for s in self.sessions.values() if s.type == "private"),
                "conversations": sum(len(s.conversations) for s in self.sessions.values()),
            }
