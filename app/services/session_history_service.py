"""会话历史服务：为 WebUI 提供会话列表 / 对话线程 / 编辑 / 导出 / 图片管理。

面板使用独立的 HistoryStore 访问本地归档；遇到仍在内存中的活跃会话时，
会同步修改 SessionManager 里的 Conversation，避免 UI 修改被下一次保存覆盖。

图片（2026-10 起）：会话里的图片存成**本地文件 + 一个指向**（``data.media`` 的 sha，
见 :mod:`app.llm.image_media`）。模型请求与前端界面读的是同一个指向，所以界面上的
"看 / 换 / 删"就是对同一份字节动手；删文件必须先把引用删掉（先移除/替换那张图）。
"""

from __future__ import annotations

import io
import json
import os
import uuid
import zipfile
from typing import Any

from app.llm import image_media
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
        """返回某个会话及其全部对话线程。

        ``conversations`` 为空是**正常状态**：活跃会话里唯一的对话被删/被清空后，归档里
        这条会话就没有任何行了。此时返回空列表而不是 ``None``——对界面来说
        "这个会话现在没有对话" 与 "这个会话 id 不存在" 不是一回事，前者不该报错。
        """
        tasks = self.history.list_tasks(session_id)
        if not tasks:
            return {
                "session_id": session_id,
                "type": "",
                "conversations": [],
                "empty": True,
            }
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
            "empty": False,
        }

    def get_conversation(self, task_id: str) -> dict | None:
        """返回完整对话归档（含 messages 数组），每条消息附带图片清单 ``images``。"""
        data = self.history.load_history(task_id)
        if data is None:
            return None
        messages = data.get("messages")
        if isinstance(messages, list):
            data["messages"] = [self._decorate_message(m) for m in messages]
        return data

    # ---------- 图片（本地仓） ----------

    def _media_url(self, sha: str, mime: str = "") -> str:
        """本地图片的访问地址（前端按需拼 ``?bot_id=&token=``）。"""
        ext = image_media.EXT_BY_MIME.get(str(mime or ""), "")
        return f"/api/media/{sha}.{ext}" if ext else f"/api/media/{sha}"

    def _decorate_message(self, message: Any) -> Any:
        """给消息补一份图片清单：``seg`` 是段下标（改/删时回传给后端）+ 状态与访问地址。"""
        if not isinstance(message, dict):
            return message
        images: list[dict] = []
        for index, seg in enumerate(image_media.entry_segments(message)):
            if image_media.seg_type(seg) not in image_media.IMAGE_LIKE_SEGMENTS:
                continue
            media = image_media.segment_media(seg)
            if not media:
                images.append({
                    "seg": index, "sha": "", "mime": "", "bytes": 0,
                    "status": "uncached", "url": "",
                })
                continue
            sha = str(media.get("sha", "")).strip().lower()
            mime = str(media.get("mime", "") or "").strip()
            exists = bool(image_media.path_of(self.bot_id, sha))
            images.append({
                "seg": index,
                "sha": sha,
                "mime": mime,
                "bytes": int(media.get("bytes", 0) or 0),
                "status": "cached" if exists else "missing",
                "url": self._media_url(sha, mime) if exists else "",
            })
        decorated = dict(message)
        decorated["images"] = images
        return decorated

    def media_stats(self) -> dict:
        """本地图片占用情况（张数 / 总大小 / 被引用数 / 可清理数）。"""
        return image_media.stats(self.bot_id)

    def delete_media(self, sha: str) -> dict:
        """删除一个本地图片；仍被会话引用时拒绝并返回引用位置。"""
        return image_media.delete_media(self.bot_id, sha)

    def purge_media(self) -> dict:
        """清理所有"没人引用"的本地图片（被引用的永不自动删）。"""
        return image_media.purge_unreferenced(self.bot_id)

    def gc_media(self) -> dict:
        """按 TTL + 容量上限清理（被引用的不删）。"""
        return image_media.gc(
            self.bot_id,
            ttl_days=image_media.DEFAULT_TTL_DAYS,
            max_mb=image_media.DEFAULT_MAX_MB,
        )

    def replace_message_media(
        self, session_id: str, task_id: str, index: int, seg_index: int, data: bytes, mime: str = ""
    ) -> dict:
        """替换某条消息里第 ``seg_index`` 个图片段的内容（新图落盘 → 段指向新 sha）。

        内容寻址的副作用是"替换"= 写一个新 sha 并改指向；旧图若没有别的消息在用，
        就变成"未引用"状态，等清理时回收（不会立刻删，避免误伤共享同一张图的其它消息）。
        """
        if not data:
            raise ValueError("上传内容为空")
        saved = image_media.save_bytes(self.bot_id, data, mime=mime)
        if not saved:
            raise ValueError("图片内容无法识别或落盘失败")
        messages = self._load_messages(task_id)
        message = self._message_at(messages, index)
        segments = self._segments_of(message)
        seg = self._image_segment(segments, seg_index)
        seg_data = image_media.seg_data(seg)
        seg["data"] = {**seg_data, "media": dict(saved)}
        image_media.strip_remote_addresses(seg["data"])
        data_after = self.history.update_history(task_id, messages=messages)
        if data_after is None:
            raise ValueError("对话不存在")
        self._sync_active_messages(session_id, task_id, messages)
        return {"conversation": self.get_conversation(task_id), "media": saved}

    def remove_message_media(self, session_id: str, task_id: str, index: int, seg_index: int) -> dict:
        """移除某条消息里的第 ``seg_index`` 个图片段（引用消失后那张图就能删了）。"""
        messages = self._load_messages(task_id)
        message = self._message_at(messages, index)
        segments = self._segments_of(message)
        seg = self._image_segment(segments, seg_index)
        removed = image_media.segment_media(seg)
        del segments[seg_index]
        # 纯图片消息被删光后不能留一条空壳：给一句占位正文，避免历史里出现"空消息"
        if not segments and not str(message.get("content") or "").strip():
            message["content"] = "[图片已移除]"
            base = message.get("base")
            if isinstance(base, dict):
                base["text"] = "[图片已移除]"
        data_after = self.history.update_history(task_id, messages=messages)
        if data_after is None:
            raise ValueError("对话不存在")
        self._sync_active_messages(session_id, task_id, messages)
        return {
            "conversation": self.get_conversation(task_id),
            "removed": removed or {},
            "sha": str((removed or {}).get("sha", "")),
        }

    # ---------- 图片编辑的小工具 ----------

    def _load_messages(self, task_id: str) -> list:
        data = self.history.load_history(task_id)
        if data is None:
            raise ValueError("对话不存在")
        return list(data.get("messages") or [])

    @staticmethod
    def _message_at(messages: list, index: int) -> dict:
        try:
            position = int(index)
        except (TypeError, ValueError):
            raise ValueError("消息索引不合法")
        if position < 0 or position >= len(messages):
            raise ValueError(f"消息索引越界: {position}")
        message = messages[position]
        if not isinstance(message, dict):
            raise ValueError("该消息结构不支持图片编辑")
        return message

    @staticmethod
    def _segments_of(message: dict) -> list:
        base = message.get("base")
        if isinstance(base, dict) and isinstance(base.get("segments"), list):
            return base["segments"]
        segments = message.get("segments")
        if isinstance(segments, list):
            return segments
        raise ValueError("该消息没有消息段，无法编辑图片")

    @staticmethod
    def _image_segment(segments: list, seg_index: int) -> dict:
        try:
            position = int(seg_index)
        except (TypeError, ValueError):
            raise ValueError("图片段索引不合法")
        if position < 0 or position >= len(segments):
            raise ValueError(f"图片段索引越界: {position}")
        seg = segments[position]
        if image_media.seg_type(seg) not in image_media.IMAGE_LIKE_SEGMENTS:
            raise ValueError("指定的段不是图片")
        return seg

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

    def delete_conversation(self, session_id: str, task_id: str) -> dict:
        """删除对话归档，返回 ``{"deleted": bool, "mode": "deleted"|"cleared"}``。

        两条路径以前不一致（活跃会话的唯一对话直接报 400 "不能删除"，归档会话却删得
        干干净净、会话随之从列表消失），用户看到的是"同一个按钮有时删得掉、有时报错"。
        现在口径统一：

        - 归档存在 → 删归档（``mode="deleted"``）；
        - 它同时是**内存里活跃会话的唯一对话** → 就地把该对话清空并换新 ``task_id``
          （``mode="cleared"``）。内存里"至少保留一个活跃对话"的不变式仍然成立，
          只是不再把它变成用户可见的错误。
        """
        data = self.history.load_history(task_id)
        if data is None:
            return {"deleted": False, "mode": "missing"}

        session, conv = self._find_active_conversation(session_id, task_id)
        cleared = False
        if session is not None and conv is not None:
            if len(session.conversations) <= 1:
                # 活跃会话：清空消息 + 换 task_id，等于"从历史里消失、会话继续跑"
                conv.data.history = []
                conv.title = ""
                conv.task_id = uuid.uuid4().hex[:16]
                session.touch()
                self.history.delete_history(task_id)
                cleared = True
            else:
                session.delete_conversation(conv.id)
                session.touch()
                self.history.save_session(session)
                self.history.delete_history(task_id)
                cleared = False
        else:
            self.history.delete_history(task_id)

        return {"deleted": True, "mode": "cleared" if cleared else "deleted"}

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
        result = {"deleted": [], "failed": [], "cleared": []}
        for tid in task_ids:
            try:
                outcome = self.delete_conversation(session_id, tid)
                if not outcome.get("deleted"):
                    result["failed"].append({"task_id": tid, "error": "对话不存在"})
                elif outcome.get("mode") == "cleared":
                    result["cleared"].append(tid)
                    result["deleted"].append(tid)
                else:
                    result["deleted"].append(tid)
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

    @staticmethod
    def _iter_message_media(conversations: list) -> list[dict]:
        """备份里引用到的全部图片指向（去重）。"""
        seen: set[str] = set()
        media_list: list[dict] = []
        for conv in conversations or []:
            for message in (conv or {}).get("messages") or []:
                for seg in image_media.entry_segments(message):
                    media = image_media.segment_media(seg)
                    if not media:
                        continue
                    sha = str(media.get("sha", "")).strip().lower()
                    if not sha or sha in seen:
                        continue
                    seen.add(sha)
                    media_list.append(media)
        return media_list

    def export_session_bundle(self, session_id: str) -> tuple[bytes, str, dict] | None:
        """导出整个会话为 **zip 备份包**：``session.json`` + ``media/<sha>.<ext>``。

        为什么一定要打包：会话里的图片现在是"指向 + 本地文件"，只导 JSON 的话换台机器
        导回去全是 ``missing``（图还在但字节没跟过来）。打包后两边都完整，恢复时
        先落盘图片再导入历史，指向自然还能用。
        """
        payload = self.export_session_json(session_id)
        if payload is None:
            return None
        summary = {"conversations": len(payload.get("conversations") or []), "media": 0, "missing": 0}
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("session.json", json.dumps(payload, ensure_ascii=False, indent=2))
            for media in self._iter_message_media(payload.get("conversations") or []):
                path = image_media.path_of(self.bot_id, str(media.get("sha", "")))
                if not path:
                    summary["missing"] += 1
                    continue
                bundle.write(path, f"media/{os.path.basename(path)}")
                summary["media"] += 1
        return buffer.getvalue(), f"{session_id}.session.zip", summary

    def restore_bundle(self, blob: bytes) -> dict:
        """从 zip 备份包恢复（先落盘图片，再导入会话，指向保持可用）。"""
        if not blob:
            raise ValueError("备份文件为空")
        try:
            with zipfile.ZipFile(io.BytesIO(blob)) as bundle:
                names = bundle.namelist()
                json_name = next((n for n in names if os.path.basename(n) == "session.json"), "")
                if not json_name:
                    raise ValueError("备份包里缺少 session.json")
                payload = json.loads(bundle.read(json_name).decode("utf-8"))
                restored_media = 0
                for name in names:
                    if not name.startswith("media/") or name.endswith("/"):
                        continue
                    basename = os.path.basename(name)
                    if not image_media.SHA_RE.match(basename.split(".", 1)[0].lower()):
                        continue
                    if image_media.save_bytes(self.bot_id, bundle.read(name)):
                        restored_media += 1
        except zipfile.BadZipFile as e:
            raise ValueError(f"备份文件不是有效的 zip: {e}")
        result = self.restore_session(payload)
        result["media"] = restored_media
        return result

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
