"""消息跟随业务逻辑：达到 N 条相同消息后自动复读。"""

import hashlib
import json

from app.core.logger import module_logger


def _content_key(data) -> str:
    """消息内容的稳定哈希 key（不依赖段顺序，避免大内容进缓存 key）。"""
    return "follow_" + hashlib.md5(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


async def handle(module, event):
    logger = module_logger.add_info(f"#{module.bot_id}").add_info(f"{module.name}")
    cache = module.ctx.services.cache

    user_id = event.user_id
    group_id = event.group.group_id
    if not event.message_id:
        return
    message_data = event.message
    if user_id == event.self_id:
        # 自己发送的消息不参与
        return

    config = module.config
    follow_msg_count = config.get("follow_msg_count", 3)
    follow_msg_type = config.get("follow_msg_type", "text")
    follow_msg_time = config.get("follow_msg_time", 600)

    message_text = {}
    for seg in message_data:
        if seg.type not in ("text", "at", "reply", "image"):
            continue
        if seg.type == "image":
            message_text["image"] = {
                "type": "image",
                "data": {
                    "file": seg.data.get("file", ""),
                    "summary": seg.data.get("summary", "[动画表情]"),
                    "cache": 0,
                    "sub_type": seg.data.get("sub_type", ""),
                },
            }
        else:
            message_text[seg.type] = {"type": seg.type, "data": dict(seg.data)}

    msg_cache_data = []
    if follow_msg_type == "text":
        msg_cache_data.append(message_text.get("text", {}))
    elif follow_msg_type == "image":
        msg_cache_data.append(message_text.get("image", {}))
    elif follow_msg_type == "text_image":
        msg_cache_data.append(message_text.get("image", message_text.get("text")))
    else:
        logger.error(f"未知的配置项, {follow_msg_type}")
        return

    if not msg_cache_data:
        return

    if not cache.has(_content_key(msg_cache_data)):
        cache.set(_content_key(msg_cache_data), event.message_id, follow_msg_time)
        cache.set(f"{event.message_id}_data", msg_cache_data, follow_msg_time)
        msgid = event.message_id
    else:
        msgid = cache.get(_content_key(msg_cache_data))
        cache.touch(_content_key(msg_cache_data))
        cache.touch(f"{msgid}_data")

    count_type = 0
    while True:
        count_type += 1
        cache_type = f"{msgid}_{count_type}"
        if cache.get(cache_type) == user_id:
            return
        if not cache.has(cache_type):
            break
        cache.touch(cache_type)

    cache.set(cache_type, user_id, follow_msg_time)
    logger.debug(f"标记缓存数据: {cache_type} {user_id}")

    if not count_type >= follow_msg_count:
        return
    if cache.has(f"{msgid}_processed"):
        cache.touch(f"{msgid}_processed")
        return

    cache.set(f"{msgid}_processed", True, follow_msg_time)
    msg_data = cache.get(f"{msgid}_data")
    if not msg_data:
        logger.error(f"消息数据为空, {msgid}:{msg_data}")
        return
    await event.bot.send_group_msg(group_id=group_id, message=msg_data)
