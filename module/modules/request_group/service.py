"""群申请处理业务逻辑：按群配置的关键词 + 等级自动通过/拒绝。"""

import asyncio
import json

from app.core.logger import module_logger


async def handle(module, event):
    logger = module_logger.add_info(f"#{module.bot_id}").add_info(f"{module.name}")
    group_id = event.group_id
    user_id = event.user_id

    group_keywords_data = module.config.get("group_keywords_data", {})
    if not group_keywords_data:
        return
    if isinstance(group_keywords_data, str):
        try:
            group_keywords_data = json.loads(group_keywords_data)
        except json.JSONDecodeError as e:
            logger.error(f"配置解析错误: {e}")
            return

    group_cfg = group_keywords_data.get(str(group_id), {}) or {}
    group_keywords = group_cfg.get("keywords", {})
    if not group_keywords:
        logger.info(f"没有关键词配置: {group_id}")
        return
    keywords_type = group_keywords.get("type", "refuse")  # refuse ignore
    keywords_data = group_keywords.get("data", {})

    group_level = group_cfg.get("level", {})
    level_data = group_level.get("value", 0)
    level_type = group_level.get("type", "refuse")

    if_accept = group_cfg.get("if_accept", {})
    if_accept_type = if_accept.get("type", "accept")  # accept refuse

    comment = event.comment

    async def _is_level(user_id, event):
        user_info = await event.bot.get_stranger_info(user_id)
        user_data = (user_info or {}).get("data", {})
        if not user_data:
            logger.warning(f"没有用户数据: {user_id}")
            return False, 0
        userinfo = {
            "user_level": user_data.get("qqLevel", 0),
            "user_name": user_data.get("nick", "未知用户"),
        }
        if userinfo["user_level"] < level_data:
            return False, userinfo
        return True, userinfo

    is_keywords = _if_keywords(keywords_data, comment)
    is_level, userinfo = await _is_level(user_id, event)

    reply_type = "ignore"
    reply_text = ""
    if not is_keywords:
        if keywords_type == "ignore":
            reply_type = "ignore"
            reply_text += " 入群问题回答错误"
        elif keywords_type == "refuse":
            reply_type = "refuse"
            reply_text = "入群问题回答错误"

    if not is_level:
        if level_type == "ignore":
            reply_type = "ignore"
            reply_text += " 等级不足"
        elif level_type == "refuse":
            reply_type = "refuse"
            reply_text = f"账号等级过低，不足 {level_data}"

    if is_keywords and is_level:
        if if_accept_type == "accept":
            reply_type = "accept"
            reply_text = "入群申请通过"
        elif if_accept_type == "ignore":
            reply_type = "ignore"
            reply_text = "满足入群条件，忽略"

    await process_request(module, event, reply_type, reply_text, userinfo)


async def process_request(module, event, reply_type, reply_text, userinfo):
    logger = module_logger.add_info(f"#{module.bot_id}").add_info(f"{module.name}")
    request_flag = event.flag
    user_id = event.user_id
    logger.info(
        f"处理申请 | {userinfo['user_name']}({user_id}) | 结果: {reply_type} "
        f"| 等级: {userinfo['user_level']} 理由:{reply_text}"
    )

    if reply_type == "ignore":
        return
    approve = reply_type == "accept"
    await asyncio.sleep(0.2)
    await event.bot.set_group_add_request(flag=request_flag, approve=approve, reason=reply_text)


def _if_keywords(keywords_data, comment):
    from app.modules import match_keywords

    if not comment:
        return []
    return match_keywords(keywords_data, comment.split())
