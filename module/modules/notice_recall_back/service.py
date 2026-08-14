"""防撤回业务逻辑：内存 + 磁盘双层缓存，撤回时以合并转发形式转发到目标。

- 消息事件 → 内存缓存（cache_time）+ 持久化库（db_enable，重启兜底）；
- 撤回事件 → 先查内存、未命中查磁盘，构建「提示节点 + 被撤回消息节点」合并转发。
"""

from __future__ import annotations

import os

from app.core.logger import module_logger
from app.modules import get_data_path, resolve_enabled_ids


def get_forward_nodes(forward_msg):
    """递归构建 node 格式消息节点，支持多层嵌套合并转发。"""
    msg_nodes = []
    for msg in forward_msg:
        sender = msg.get("sender", {})
        user_card = sender.get("card", "") or sender.get("user_card", "")
        user_name = user_card if user_card else sender.get("nickname", "") or sender.get("user_nickname", "")
        user_id = sender.get("user_id", "")
        msg_array = msg.get("message", [])

        has_forward = False
        nested_nodes = []
        for msg_json in msg_array:
            if msg_json.get("type") == "forward":
                has_forward = True
                forward_data = msg_json.get("data", {}).get("content", [])
                nested_nodes = get_forward_nodes(forward_data)

        if not has_forward:
            msg_nodes.append({
                "type": "node",
                "data": {"name": user_name, "uin": user_id, "content": msg_array},
            })
        else:
            content = []
            for msg_json in msg_array:
                if msg_json.get("type") == "forward":
                    content.extend(nested_nodes)
                else:
                    content.append(msg_json)
            msg_nodes.append({
                "type": "node",
                "data": {"name": user_name, "uin": user_id, "content": content},
            })
    return msg_nodes


def get_msg_text(recalled: dict):
    """把消息快照转为 node 格式消息节点（自动识别普通/合并转发）。"""
    if not recalled.get("forward_msg"):
        user_name = recalled.get("user_card") or recalled.get("user_nickname") or str(recalled.get("user_id", ""))
        return [{
            "type": "node",
            "data": {
                "name": user_name,
                "uin": recalled.get("user_id", ""),
                "content": recalled.get("message", []),
            },
        }]
    return get_forward_nodes(recalled["forward_msg"])


def build_forward_msg_data(recalled: dict, recall_event):
    """构建撤回通知的合并转发数据：提示节点 + 被撤回消息节点。"""
    user_id = recall_event.user_id
    operator_id = recall_event.operator_id
    group_id = recalled.get("group_id") or getattr(recall_event, "group_id", 0) or 0

    if operator_id == user_id:
        tip_text = f"群{group_id}的{operator_id}撤回了以下消息："
    else:
        tip_text = f"群{group_id}的{operator_id}撤回了{user_id}的消息："

    tip_node = [{"type": "node", "data": {"name": "撤回通知", "uin": 10000, "content": tip_text}}]
    msg_nodes = get_msg_text(recalled)
    return tip_node + msg_nodes


def _serialize_event(event) -> dict:
    """把消息事件序列化为可落盘的快照 dict。"""
    return {
        "message_id": event.message_id,
        "group_id": event.group.group_id,
        "user_id": event.user_id,
        "user_card": event.user.card,
        "user_nickname": event.user.nickname,
        "self_id": event.self_id,
        "message": [seg.to_dict() for seg in event.message],
        "forward_msg": event.forward_msg or [],
    }


def _db_path(module) -> str:
    """持久化库文件路径（按 bot 实例隔离）。"""
    name = f"message_db_{module.bot_id}.json" if module.bot_id is not None else "message_db_global.json"
    return os.path.join(get_data_path(module.module_name), name)


def _get_db(module):
    """懒加载模块实例的持久化库；未启用返回 None。"""
    if not module.config.get("db_enable", True):
        return None
    db = getattr(module, "_recall_db", None)
    if db is None:
        from .recall_db import RecallDB

        db = RecallDB(_db_path(module))
        module._recall_db = db
    return db


async def on_load(module) -> None:
    """启动清理：超量/过期消息淘汰。"""
    db = _get_db(module)
    if db is None:
        return
    max_total = int(module.config.get("db_max_messages", 5000) or 0)
    max_age = int(module.config.get("db_retention_minutes", 60) or 0)
    await db.cleanup(max_total=max_total, max_age_minutes=max_age)


async def handle(module, event):
    logger = module_logger.add_info(f"#{module.bot_id}").add_info(module.name)
    cache = module.ctx.services.cache

    config = module.config
    cache_time = config.get("cache_time", 600)
    if not isinstance(cache_time, int):
        return

    if event.event_type in ("message_group", "message_private"):
        # 缓存普通消息，等待撤回事件使用
        if event.user_id == event.self_id:
            return
        if cache.has(f"{event.message_id}_msgobject"):
            return
        cache.set(f"{event.message_id}_msgobject", event, cache_time)
        # 磁盘持久层（重启兜底）
        db = _get_db(module)
        if db is not None:
            await db.store(str(event.message_id), _serialize_event(event))
        return

    if event.event_type in ("notice_group_recall", "notice_private_recall"):
        recalled = cache.get(f"{event.message_id}_msgobject")
        if recalled is not None:
            recalled = _serialize_event(recalled)
        else:
            db = _get_db(module)
            if db is not None:
                data = await db.get(str(event.message_id))
                if data:
                    recalled = data
                    logger.debug(f"消息 {event.message_id} 命中磁盘缓存")
        if not recalled:
            logger.warning(f"消息 {event.message_id} 未缓存")
            return
        logger.debug(f"消息 {event.message_id} 已缓存")

        forward_msg_data = build_forward_msg_data(recalled, event)
        for gid in resolve_enabled_ids(config.get("target_groups", {}), config.get("target_groups_mode", "all")):
            logger.info(f"转发到群 {gid}")
            await event.bot.send_forward_msg(group_id=int(gid), msgdata=forward_msg_data)
        for uid in resolve_enabled_ids(config.get("target_users", {}), config.get("target_users_mode", "all")):
            logger.info(f"转发到用户 {uid}")
            await event.bot.send_forward_msg(user_id=int(uid), msgdata=forward_msg_data)
        return
