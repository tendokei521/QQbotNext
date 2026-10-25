"""会话管理模块（框架级，化用 AstrBot「会话/对话分离」）。

- Session：对话窗口（group_xxx / private_xxx），是参与者、冷却、超时的载体；
- Conversation：窗口内的对话线程，拥有独立的 history / tags / task_id，
  支持切换、删除；
- Session.data / task_id 为「当前活跃对话」的代理，保证旧调用不破坏。

## 历史存储契约（插件可依赖）

存储条目字段：

- ``role``：``user`` / ``assistant`` / ``system``（必填）
- ``content``：纯文本正文（**兼容字段**；结构化条目也保留它）
- ``time``：秒级时间戳
- ``user_id`` / ``nickname``：发送者（助手条目通常为空）
- ``message_id``：OneBot 消息句柄（发送成功后回填，用于引用/去重）
- ``base``：结构化条目才有的原始信息
  ``{time, sender_id, sender_name, group_id, is_private, text, segments, meta_lines?}``

两个读取入口的区别（迁移复刻第三方组装格式时用后者）：

- ``get_history()``：**投影视图** —— 只保留上表子集、过滤空/杂散 assistant，
  直接可喂 OpenAI 风格 messages；
- ``history_snapshot()``：**原始条目副本** —— 不投影、不渲染，插件可用自己的
  渲染器（例如 1:1 复刻 AstrBot 的历史格式）组装。

写入侧：``add_message()`` 追加单条（框架主路径用），``import_history()`` 批量
导入外部历史（迁移用；支持 AstrBot/OpenAI 风格 ``{role, content}``）。

旁路自有历史：插件**接管 agent**（``supersedes = ("agent",)``）后框架不再跑
``prepare_prompt``，也就不会再写框架历史；此时插件可以用 ``import_history`` /
``history_snapshot`` 自己管一套，或继续沿用框架这套存储。
"""

import copy
import re
import threading
import time
import uuid
from typing import Any

from app.agent import logger
from app.core.logger import account_label
from app.agent.storage import create_history_store

#: 存储层条目字段（读取侧可依赖的契约，见模块 docstring）
HISTORY_ENTRY_FIELDS = ("role", "content", "time", "user_id", "nickname", "message_id", "base")
#: 参与组装的角色白名单（导入外部历史时用）
HISTORY_ROLES = ("user", "assistant", "system")

# 防御：模型/中转偶发的孤立 "rate." 不应作为有效助手回复回灌上下文
_JUNK_ASSISTANT_RE = re.compile(r"^\s*rate\.\s*$", re.IGNORECASE)


def _is_junk_assistant(content) -> bool:
    return bool(content and _JUNK_ASSISTANT_RE.match(str(content)))


def _content_to_text(content: Any) -> str:
    """多模态 content → 纯文本（只取文本块；图片等在框架里由 segments 承载）。"""
    if isinstance(content, list):
        return "".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return str(content or "")


def normalize_import_entry(entry: Any) -> dict | None:
    """把外部历史条目归一化成框架存储形状；无法识别的返回 None。

    接受两类输入：

    - 外部/裸风格：``{"role": "user", "content": "…"}``（AstrBot ``Message`` dump、
      OpenAI messages 都是这个形状；content 为多模态数组时只取文本块）；
    - 框架存储形状：额外带 ``time`` / ``user_id`` / ``nickname`` / ``message_id`` /
      ``segments`` / ``group_id`` / ``is_private`` 时一并保留。

    ``timestamp`` 与 ``time`` 都接受；缺失时用当前时间。空正文且无 ``segments``
    的条目丢弃（框架渲染器不喜欢空条目）。
    """
    if not isinstance(entry, dict):
        return None
    role = str(entry.get("role", "") or "").strip().lower()
    if role not in HISTORY_ROLES:
        return None
    segments = entry.get("segments")
    segments = list(segments) if isinstance(segments, list) else []
    content = _content_to_text(entry.get("content"))
    if not content.strip() and not segments:
        return None

    raw_ts = entry.get("timestamp", entry.get("time"))
    try:
        ts = int(raw_ts) if raw_ts is not None else int(time.time())
    except (TypeError, ValueError):
        ts = int(time.time())

    result: dict[str, Any] = {"role": role, "time": ts, "content": content}
    if entry.get("user_id"):
        result["user_id"] = str(entry["user_id"])
    if entry.get("nickname"):
        result["nickname"] = str(entry["nickname"])
    if entry.get("message_id") not in (None, ""):
        result["message_id"] = str(entry["message_id"])
    if segments:
        result["base"] = {
            "time": ts,
            "sender_id": str(entry.get("user_id") or ""),
            "sender_name": str(entry.get("nickname") or ""),
            "group_id": str(entry.get("group_id") or ""),
            "is_private": bool(entry.get("is_private", False)),
            "text": content,
            "segments": segments,
        }
        meta_lines = entry.get("meta_lines")
        if isinstance(meta_lines, list):
            kept = [str(x) for x in meta_lines if str(x or "").strip()]
            if kept:
                result["base"]["meta_lines"] = kept
    return result


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
        # 键必须归一成字符串：`AgentRuntime` 传 str(bot_id)，而 `module.bot_id` 是 int。
        # 不归一的话模块写 `SessionManager(123)` 会拿到**另一个实例**（"123" vs 123），
        # 它写进历史的内容框架读不到 —— 表现为"模块说写了，模型说没看见"。
        key = str(bot_id)
        if key not in cls._instances:
            with cls._lock:
                if key not in cls._instances:
                    instance = super().__new__(cls)
                    instance._init(key)
                    cls._instances[key] = instance
        return cls._instances[key]

    @classmethod
    def evict(cls, bot_id: str) -> bool:
        """关闭并移除某账号的单例（换号/幽灵运行时回收时用）。返回是否移除。

        此前只有 `close()` 没有 eviction：连接换号后旧实例一直挂在 ``_instances`` 里，
        新运行时拿到的是**旧连接的历史库句柄**（"Cannot operate on a closed database"
        或写到上一个账号的历史里）。
        """
        key = str(bot_id)
        instance = cls._instances.pop(key, None)
        if instance is None:
            return False
        try:
            instance.close()
        except Exception as e:  # noqa: BLE001 —— 回收失败只留痕，不阻断调用方
            logger.debug(f"[Session] 回收 {key} 时关闭失败（已忽略）: {e}")
        return True

    def _init(self, bot_id: str):
        self.bot_id = bot_id
        self.sessions: dict[str, Session] = {}
        self.lock = threading.RLock()
        self.history = create_history_store(bot_id)
        # 流式发言的"当前这条 assistant 历史"跟踪（见 upsert_streaming_assistant）
        self._streaming_entries: dict[str, dict] = {}
        #: 流式条目的稳定 id（事件日志里靠它把多次 delta 折叠成一条）
        self._streaming_ids: dict[str, str] = {}
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
        except Exception as e:
            # 归档回调由外部注入：其异常不应影响会话归档主流程，但需留痕
            logger.debug(f"[Session] 归档回调执行失败（已忽略）: {e}")

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
        except Exception as e:
            logger.debug(f"[Session] 关闭历史存储失败（已忽略）: {e}")

    def _start_auto_cleanup(self):
        def cleanup_task():
            while not self._stop_event.is_set():
                self._stop_event.wait(30)
                if self._stop_event.is_set():
                    break
                try:
                    self._cleanup_expired()
                except Exception as e:
                    logger.add_info(account_label(self.bot_id)).warning(f"清理过期会话异常: {e}")

        self._cleanup_thread = threading.Thread(target=cleanup_task, daemon=True)
        self._cleanup_thread.start()

    def _cleanup_expired(self):
        with self.lock:
            expired = [sid for sid, s in self.sessions.items() if not s.is_alive()]
            for sid in expired:
                session = self.sessions.pop(sid)
                self.history.save_session(session)
                self._notify_archive(session)
                logger.add_info(account_label(self.bot_id)).info(
                    f"会话过期已归档: {sid}"
                )
        self._maybe_gc_media()

    #: 本地图片清理的最小间隔（引用中的图永远不会被删，这里只收无主文件）
    MEDIA_GC_INTERVAL = 6 * 3600

    def _maybe_gc_media(self) -> None:
        """按 TTL + 容量上限清一次本地图片仓（每 :attr:`MEDIA_GC_INTERVAL` 最多一次）。

        图片落盘后就成了"无人引用就没人管"的垃圾：删掉的会话/消息、被替换掉的旧图
        都还躺在 ``data/llm/<bot_id>/media/`` 里。挂在每 30 秒的清理线程上顺手做，
        比另起一个定时器省事；``gc`` 本身只删**没有会话引用**的文件，
        所以历史里的图片不会因为超时突然消失。
        """
        now = time.time()
        if now - getattr(self, "_last_media_gc", 0.0) < self.MEDIA_GC_INTERVAL:
            return
        self._last_media_gc = now
        try:
            from app.agent.media import store as image_media

            image_media.gc(self.bot_id)
        except Exception as e:  # noqa: BLE001 —— 清理失败不影响会话与对话
            logger.add_info(account_label(self.bot_id)).debug(f"本地图片清理失败（已忽略）: {e}")

    # ── 会话 CRUD ─────────────────────────────────────────
    def create_session(self, session_id: str, session_type: str, timeout: int = 60) -> Session:
        self.restart_cleanup()  # 懒恢复清理线程（bot 重连/框架重启后）
        session = Session(session_id, session_type, timeout)
        with self.lock:
            self.sessions[session_id] = session
        logger.add_info(account_label(self.bot_id)).info(f"创建会话: {session_id}")
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
                logger.add_info(account_label(self.bot_id)).info(f"会话已结束: {session_id}")

    def forget_session(self, session_id: str) -> bool:
        """从内存中移除会话，但不写回归档。

        用于「删除整个会话」等需要彻底清掉归档的操作，
        避免 destroy_session 先保存再删除导致历史又被写回。
        """
        with self.lock:
            exist = session_id in self.sessions
            if exist:
                self.sessions.pop(session_id, None)
                logger.add_info(account_label(self.bot_id)).info(f"会话已从内存移除: {session_id}")
            return exist

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
        *,
        segments: list | None = None,
        group_id: Any = None,
        is_private: bool = False,
        meta_lines: list[str] | None = None,
    ):
        """追加一条历史。

        传入 ``segments`` 时写入**结构化形状**（``base`` + 顶层 ``content`` 兼容字段）：
        基础信息（时间/发送者/群号/正文/原始段）与正文分离，渲染时再组装，因此
        格式/脱敏/补全都能在读取侧决定；不传 ``segments`` 时保持旧形状（仅 content）。
        """
        session = self.get_session(session_id)
        if session and session.data is not None:
            if role == "assistant" and _is_junk_assistant(content):
                return
            now = int(time.time()) if timestamp is None else int(timestamp)
            entry: dict[str, Any] = {"role": role, "time": now}
            if segments:
                entry["base"] = {
                    "time": now,
                    "sender_id": str(user_id or ""),
                    "sender_name": str(nickname or ""),
                    "group_id": str(group_id or ""),
                    "is_private": bool(is_private),
                    "text": str(content or ""),
                    "segments": list(segments),
                }
                if meta_lines:
                    entry["base"]["meta_lines"] = [str(x) for x in meta_lines if str(x or "").strip()]
                # 兼容字段：老渲染/老读者（含 UI 导出、记忆蒸馏）仍能按 content 读到本条
                entry["content"] = str(content or "")
            else:
                entry["content"] = content

            if user_id:
                entry["user_id"] = str(user_id)
            if nickname:
                entry["nickname"] = str(nickname)
            if message_id not in (None, ""):
                entry["message_id"] = str(message_id)
            history = session.data.history
            # 稳定 entry_id：事件日志里"这条消息"的身份。跟着 entry 一起存，
            # WebUI 按 id 编辑/删除时就能精确折叠到它（老数据没有，用 task:idx 兜底）。
            entry_id = uuid.uuid4().hex
            entry["entry_id"] = entry_id
            history.append(entry)
            # P4：先记账再进内存列表。事件日志是**唯一真相**，列表是它的活投影；
            # 这里双写而不是只写信日志，是为了让所有既有读取方（WebUI/统计/蒸馏）
            # 这一版完全不动。追加失败只留痕，绝不影响一条回复。
            self._log_event(
                session,
                "assistant/message" if role == "assistant" else "user/message",
                entry=entry,
                entry_id=entry_id,
            )
            if len(history) > self.MAX_HISTORY_MESSAGES:
                dropped = len(history) - self.MAX_HISTORY_MESSAGES
                del history[:dropped]
                logger.add_info(account_label(self.bot_id)).debug(
                    f"历史裁剪: {session_id} 丢弃最旧 {dropped} 条 (上限 {self.MAX_HISTORY_MESSAGES})"
                )
            session.touch()

    def upsert_streaming_assistant(self, session_id: str, text: str) -> bool:
        """流式路径专用：把本轮 assistant 正文写进历史，**支持边发边更新**。

        为什么要能更新而不是只追加：流式是一句一句 yield 出去的，而历史里这条发言
        只有一条。若等全部发完才 ``add_message``，pipeline 在每片发送成功后调
        ``mark_last_assistant_message_id`` 时「最近一条 assistant」还是**上一轮**的，
        句柄于是全补在上一轮的发言上（引用指错、群聊环境记录按 message_id 去重失效）。

        第一次调用插入一条（此刻它就是"最近一条 assistant"），后续调用只改同一条的
        正文。条目用对象身份跟踪（历史裁剪把列表切片后引用失效，此时下一个分片会
        重新插入一条，最多多一条空壳，不会写坏别人的条目）。
        """
        session = self.get_session(session_id)
        if not session or session.data is None:
            return False
        content = str(text or "")
        if not content.strip() or _is_junk_assistant(content):
            return False
        history = session.data.history
        entry = self._streaming_entries.get(session_id)
        entry_id = self._streaming_ids.get(session_id, "")
        if entry is None or not any(entry is e for e in history):
            entry = {"role": "assistant", "time": int(time.time()), "content": content}
            history.append(entry)
            self._streaming_entries[session_id] = entry
            # 同一条流式发言只有一个 entry_id：多次 delta 在投影里折叠成一条
            entry_id = f"{session.task_id}:stream:{len(history)}" if getattr(session, "task_id", "") else ""
            self._streaming_ids[session_id] = entry_id
            if entry_id:
                entry["entry_id"] = entry_id
            if len(history) > self.MAX_HISTORY_MESSAGES:
                dropped = len(history) - self.MAX_HISTORY_MESSAGES
                del history[:dropped]
                logger.add_info(account_label(self.bot_id)).debug(
                    f"历史裁剪: {session_id} 丢弃最旧 {dropped} 条 (上限 {self.MAX_HISTORY_MESSAGES})"
                )
        else:
            entry["content"] = content
        # P4：逐句更新记成 `assistant/delta`（同一 entry_id 折叠），不再"原地改一行"
        self._log_event(
            session, "assistant/delta", entry=dict(entry), entry_id=entry_id or ""
        )
        session.touch()
        return True

    def end_streaming_assistant(self, session_id: str) -> None:
        """本轮流式发言结束：释放 upsert 的跟踪标记（下轮重新插入新条目）。"""
        self._streaming_entries.pop(session_id, None)
        self._streaming_ids.pop(session_id, None)

    def mark_last_assistant_message_id(self, session_id: str, message_id: Any) -> bool:
        """把最近一条 assistant 历史补上 ``message_id``（发送成功后调用）。

        **为什么需要**：机器人自己发出的消息以前在历史里是无句柄的，于是
        "这条是它说的"无法被引用、也无法在群聊记录里做正文去重（同一句话会以
        环境视角再出现一份）。这里按"最近一条 assistant"定位——只在**发送之后**
        调用，此时它必然是刚发出去的那条。
        """
        mid = str(message_id or "").strip()
        if not mid:
            return False
        session = self.get_session(session_id)
        if not session or session.data is None:
            return False
        for entry in reversed(session.data.history):
            if entry.get("role") != "assistant":
                continue
            if str(entry.get("message_id") or "") == mid:
                return False  # 已经打过同一个 id，不重复写
            entry["message_id"] = mid
            # P4：句柄是"对已有发言的补充事实"，记成事件，不改写已写入的消息体
            self._log_event(session, "assistant/message_id", message_id=mid)
            session.touch()
            return True
        return False

    def _log_event(self, session, type_: str, **kwargs) -> None:
        """把一条历史事件追加进事件日志（P4 单一真相）。

        - 存储层没有 `append_event`（测试/旧 JSON 替身）时静默跳过，行为与改造前一致；
        - 任何异常只留痕：记日志失败绝不能把一条回复搞没。
        """
        store = getattr(self, "history", None)
        task_id = str(getattr(session, "task_id", "") or "")
        if store is None or not task_id or not hasattr(store, "append_event"):
            return
        try:
            from app.agent.session.session_log import SessionLog

            SessionLog(store, task_id).append(type_, **kwargs)
        except Exception as e:  # noqa: BLE001 —— 记账是旁路，失败不影响本轮
            logger.add_info(account_label(self.bot_id)).debug(
                f"[History] 事件追加失败（已忽略）: {e}"
            )

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
            for key in ("user_id", "nickname", "message_id", "time", "base", "turn"):
                if m.get(key) is not None:
                    item[key] = m[key]
            result.append(item)
        return result

    # ── 多对话操作 ────────────────────────────────────────
    def history_snapshot(
        self,
        session_id: str,
        *,
        limit: int | None = None,
        include_junk: bool = False,
    ) -> list[dict]:
        """返回**未投影的原始历史条目副本**（插件自带渲染器组装用）。

        与 ``get_history()`` 的区别：不做字段投影、不渲染、不裁字段——``base``
        里的 ``segments`` / ``meta_lines`` 原样保留，因此插件可以按自己的格式
        （例如 1:1 复刻 AstrBot 的 ``[昵称/时间]: 正文``）重新渲染。

        默认仍过滤已知杂散 assistant（空内容 / ``rate.``），``include_junk=True``
        时原样返回全部；返回的是深拷贝，改它不会动到存储。
        """
        session = self.get_session(session_id)
        if not session or session.data is None:
            return []
        items = list(session.data.history)
        if not include_junk:
            items = [
                m for m in items
                if not (
                    m.get("role") == "assistant"
                    and (not str(m.get("content") or "").strip() or _is_junk_assistant(m.get("content")))
                )
            ]
        if limit is not None:
            items = items[-limit:] if limit > 0 else []
        return [copy.deepcopy(m) for m in items]

    def import_history(
        self,
        session_id: str,
        entries: list[Any],
        *,
        replace: bool = False,
        session_type: str = "group",
        timeout: int = 60,
        persist: bool = True,
    ) -> dict:
        """把外部历史批量写入本框架会话（迁移用）。

        - 条目可为 AstrBot/OpenAI 风格 ``{role, content}`` 或框架存储形状
          （见 ``normalize_import_entry``）；
        - ``replace=True`` 覆盖当前对话历史，否则追加；
        - 会话不存在时按 ``session_type`` 新建（迁移时不必先造会话）；
        - 无法识别的条目**计数丢弃而不抛异常**：迁移要能"尽量搬、别中断"；
        - 超出 ``MAX_HISTORY_MESSAGES`` 时从最旧开始丢。

        返回 ``{"imported", "skipped", "dropped", "total"}``。
        """
        raw = list(entries or [])
        normalized = [e for e in (normalize_import_entry(x) for x in raw) if e is not None]

        session = self.get_session(session_id)
        if session is None:
            session = self.create_session(session_id, session_type, timeout)
        if session.data is None:
            return {"imported": 0, "skipped": len(raw), "dropped": 0, "total": 0}

        with self.lock:
            history = session.data.history
            if replace:
                history.clear()
            history.extend(normalized)
            dropped = 0
            if len(history) > self.MAX_HISTORY_MESSAGES:
                dropped = len(history) - self.MAX_HISTORY_MESSAGES
                del history[:dropped]
            session.touch()

        if persist:
            self.history.save_session(session)
        logger.add_info(account_label(self.bot_id)).info(
            f"导入外部历史: {session_id} 写入 {len(normalized)} 条"
            f"（丢弃 {len(raw) - len(normalized)}，裁剪 {dropped}）"
        )
        return {
            "imported": len(normalized),
            "skipped": len(raw) - len(normalized),
            "dropped": dropped,
            "total": len(session.data.history),
        }

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
        logger.add_info(account_label(self.bot_id)).info(
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
