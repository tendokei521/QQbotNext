"""防撤回业务逻辑：缓存消息，撤回时以合并转发形式转发到目标。"""

from app.core.logger import module_logger
from app.modules import resolve_enabled_ids


def get_forward_nodes(forward_msg):
    """递归构建 node 格式消息节点，支持多层嵌套合并转发。"""
    msg_nodes = []
    for msg in forward_msg:
        sender = msg.get("sender", {})
        user_card = sender.get("user_card", "")
        user_name = user_card if user_card else sender.get("user_nickname", "")
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


def get_msg_text(event):
    """把消息事件转为 node 格式消息节点（自动识别普通/合并转发）。"""
    if not event.forward_msg:
        user_name = event.user.card or event.user.nickname
        return [{
            "type": "node",
            "data": {
                "name": user_name,
                "uin": event.user_id,
                "content": [seg.to_dict() for seg in event.message],
            },
        }]
    return get_forward_nodes(event.forward_msg)


def build_forward_msg_data(recalled_event, recall_event):
    """构建撤回通知的合并转发数据：提示节点 + 被撤回消息节点。"""
    user_id = recall_event.user_id
    operator_id = recall_event.operator_id
    group_id = recalled_event.group.group_id

    if operator_id == user_id:
        tip_text = f"群{group_id}的{operator_id}撤回了以下消息："
    else:
        tip_text = f"群{group_id}的{operator_id}撤回了{user_id}的消息："

    tip_node = [{"type": "node", "data": {"name": "撤回通知", "uin": 10000, "content": tip_text}}]
    msg_nodes = get_msg_text(recalled_event)
    return tip_node + msg_nodes


async def handle(module, event):
    logger = module_logger.add_info(f"#{module.bot_id}").add_info(f"{module.name}")
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
        return

    if event.event_type in ("notice_group_recall", "notice_private_recall"):
        recalled = cache.get(f"{event.message_id}_msgobject")
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
