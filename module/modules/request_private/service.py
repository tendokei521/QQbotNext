"""私聊（好友）申请处理业务逻辑：按关键词 + 等级自动通过/拒绝。

注意：原实现存在「变量先于定义使用」的缺陷，迁移时已修复顺序。
"""

import asyncio
import json

from app.core.logger import module_logger


async def handle(module, event):
    logger = module_logger.add_info(f"#{module.bot_id}").add_info(f"{module.name}")
    cache = module.ctx.services.cache

    user_id = event.user_id
    self_id = event.self_id

    private_keywords_data = module.config.get("private_keywords_data", {})
    if not private_keywords_data:
        logger.warning(f"没有关键词配置: {user_id}")
        return
    if isinstance(private_keywords_data, str):
        try:
            private_keywords_data = json.loads(private_keywords_data)
        except json.JSONDecodeError as e:
            logger.error(f"配置解析错误: {e}")
            return

    private_keywords = private_keywords_data.get("keywords", {})
    keywords_type = private_keywords.get("type", "refuse")  # refuse ignore
    keywords_data = private_keywords.get("data", {})

    private_level = private_keywords_data.get("level", {})
    level_data = private_level.get("value", 0)
    level_type = private_level.get("type", "refuse")

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

    is_keywords = _if_keywords(keywords_data, comment, self_id, user_id)
    is_level, userinfo = await _is_level(user_id, event)

    reply_type = "ignore"
    reply_text = ""
    if not is_keywords:
        if keywords_type == "ignore":
            reply_type = "ignore"
            reply_text += " 好友申请拒绝"
        elif keywords_type == "refuse":
            reply_type = "refuse"
            reply_text = "好友申请拒绝"

    if not is_level:
        if level_type == "ignore":
            reply_type = "ignore"
            reply_text += " 等级不足"
        elif level_type == "refuse":
            reply_type = "refuse"
            reply_text = f"账号等级过低，不足 {level_data}"

    if is_keywords and is_level:
        reply_type = "accept"
        reply_text = "好友申请通过"

    await process_request(module, event, reply_type, reply_text, userinfo, cache)


async def process_request(module, event, reply_type, reply_text, userinfo, cache):
    logger = module_logger.add_info(f"#{module.bot_id}").add_info(f"{module.name}")
    request_flag = event.flag
    user_id = event.user_id
    logger.info(
        f"处理好友申请 | {userinfo['user_name']}({user_id}) | 结果: {reply_type} "
        f"| 等级: {userinfo['user_level']} 理由:{reply_text}"
    )

    if reply_type == "ignore":
        return
    approve = reply_type == "accept"
    await asyncio.sleep(0.2)
    await event.bot.set_friend_add_request(flag=request_flag, approve=approve)
    # 通过后忽略一条 bot 自己的提示消息
    cache.set(f"message_private_{user_id}_ignore", 1, 60)


def _if_keywords(keywords_data, comment, self_id, user_id):
    from app.modules import match_keywords

    if not comment:
        return []
    return match_keywords(keywords_data, comment.split(), self_id=self_id, user_id=user_id)
