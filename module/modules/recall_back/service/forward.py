"""转发消息构建（纯函数）：提示节点 + 被撤回消息节点，支持嵌套合并转发递归。"""


def get_forward_nodes(forward_msg):
    """递归构建 node 格式消息节点，支持多层嵌套合并转发（多个 forward 段全部展开）。"""
    msg_nodes = []
    for msg in forward_msg:
        sender = msg.get("sender", {})
        user_card = sender.get("card", "") or sender.get("user_card", "")
        user_name = user_card if user_card else sender.get("nickname", "") or sender.get("user_nickname", "")
        user_id = sender.get("user_id", "")
        msg_array = msg.get("message", [])

        nested_nodes = []
        for msg_json in msg_array:
            if msg_json.get("type") == "forward":
                nested_nodes.extend(get_forward_nodes(msg_json.get("data", {}).get("content", [])))

        if not nested_nodes:
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
    # 合并转发消息：外层包「转发者」node，内层递归展开（保持「A 转发了聊天记录」的层级）
    user_name = recalled.get("user_card") or recalled.get("user_nickname") or str(recalled.get("user_id", ""))
    return [{
        "type": "node",
        "data": {
            "name": user_name,
            "uin": recalled.get("user_id", ""),
            "content": get_forward_nodes(recalled["forward_msg"]),
        },
    }]


def build_forward_msg_data(recalled: dict, recall_event):
    """构建撤回通知的合并转发数据：提示节点 + 被撤回消息节点。

    私聊撤回（无群号）时提示文本省略「群xx」前缀。
    """
    user_id = recall_event.user_id
    operator_id = recall_event.operator_id
    group_id = recalled.get("group_id") or getattr(recall_event, "group_id", 0) or 0

    if operator_id == user_id:
        tip_text = f"群{group_id}的{operator_id}撤回了以下消息：" if group_id else f"{operator_id}撤回了以下消息："
    else:
        tip_text = (
            f"群{group_id}的{operator_id}撤回了{user_id}的消息：" if group_id
            else f"{operator_id}撤回了{user_id}的消息："
        )

    tip_node = [{"type": "node", "data": {"name": "撤回通知", "uin": 10000, "content": tip_text}}]
    msg_nodes = get_msg_text(recalled)
    return tip_node + msg_nodes
