"""关键词自回复业务逻辑（支持 and/or/at 组合匹配，动作 reply/emoji/poke）。"""

import json

from app.core.logger import module_logger

SUPPORTED_ACTION_TYPES = ["reply", "emoji", "poke"]


async def handle(module, event):
    logger = module_logger.add_info(f"#{module.bot_id}").add_info(f"{module.name}")

    customjson = module.config.get("customtext", "")
    if isinstance(customjson, str):
        try:
            customjson = json.loads(customjson)
        except json.JSONDecodeError:
            logger.error(f"配置解析错误: {customjson}")
            return
    if not customjson:
        return

    for jsondata in customjson:
        try:
            await keyword_action(module, jsondata, event)
        except Exception as e:
            logger.error(f"配置解析失败: {jsondata} {e}")


async def keyword_action(module, jsondata, event):
    logger = module_logger.add_info(f"#{module.bot_id}").add_info(f"{module.name}")
    keywords_data = jsondata.get("keywords", {})
    action = jsondata.get("action", {})
    action_type = action.get("type", "")
    if not (keywords_data and action):
        return
    if not action_type:
        logger.warning("操作类型不能为空")
        return

    try:
        in_keywords = _if_keywords(keywords_data, event)
    except Exception as e:
        logger.error(f"关键词识别失败: {e}")
        return
    if not in_keywords:
        return
    if action_type not in SUPPORTED_ACTION_TYPES:
        return
    logger.info(f"关键词识别: {in_keywords} Action: {action_type}")
    await get_action(action, event)


async def get_action(action, event):
    logger = module_logger.add_info(f"#{module.bot_id}").add_info("KeywordReply")
    user_id = event.user_id
    username = event.user.card or event.user.nickname
    group_id = event.group.group_id
    message_id = event.message_id
    action_values = action.get("values", {})
    action_type = action.get("type", "")
    text = action_values.get("text", "")
    reply = action_values.get("reply", False)
    at = action_values.get("at", "")
    atlist = []
    if at == "sender":
        atlist.append(user_id)
    elif at == "target":
        for seg in event.message:
            if seg.type == "at":
                atlist.append(seg.data.get("qq", 0))
    emoji = action_values.get("emoji", 1)

    if action_type == "reply":
        msgdata = []
        if reply:
            msgdata.append({"type": "reply", "data": {"id": message_id}})
        if atlist:
            for attarget in atlist:
                msgdata.append({"type": "at", "data": {"qq": attarget, "name": username}})
        if text:
            msgdata.append({"type": "text", "data": {"text": text}})
        await event.bot.send_group_msg(group_id=group_id, message=msgdata)
    elif action_type == "emoji":
        await event.bot.set_msg_emoji_like(message_id=message_id, emoji_id=emoji)
    elif action_type == "poke":
        await event.bot.send_poke(group_id=group_id, user_id=user_id)


def _if_keywords(keywords_data, event):
    """从消息提取文本/@ 列表后走共享关键词匹配库。"""
    from app.modules import match_keywords

    msgtext = []
    atlist = []
    for seg in event.message:
        if seg.type == "text":
            msgtext.append(seg.data.get("text", ""))
        elif seg.type == "at":
            atlist.append(seg.data.get("qq", 0))
    return match_keywords(keywords_data, msgtext, atlist=atlist, self_id=event.self_id)
