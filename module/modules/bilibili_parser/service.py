"""B 站视频解析业务逻辑。

流程：总开关 → 群范围检查 → 文本/JSON 段提取链接 → b23 短链归一
→ BV 去重 → 限数 → 获取视频信息 → 构建消息链 → 发送。
"""

from __future__ import annotations

from app.core.logger import module_logger
from . import bilibili_api as bapi


async def handle(module, event):
    logger = module_logger.add_info(f"#{module.bot_id}").add_info(module.name)
    config = module.config

    if not config.get("enable_auto_parse", True):
        return

    # 群范围检查
    group_id = event.group.group_id
    if group_id and not _check_group(config, str(group_id)):
        return

    segments = [seg.to_dict() for seg in event.message]
    if not segments:
        return

    # 1. 从文本段提取链接
    bv_list: list = []
    if config.get("enable_link_video", True):
        texts = [seg["data"].get("text", "") for seg in segments if seg.get("type") == "text"]
        bv_list.extend(bapi.extract_from_text(texts))

    # 2. 从 JSON 段（小程序卡片）提取链接
    if config.get("enable_json_video", True):
        json_parts = [seg["data"].get("data", {}) for seg in segments if seg.get("type") == "json"]
        bv_list.extend(bapi.extract_from_json(json_parts))

    if not bv_list:
        return

    # 3. 解析 b23.tv 短链，统一为 BV 号
    bv_ids = await bapi.extract_b23(bv_list)
    if not bv_ids:
        return

    # 4. BV 去重保序
    if config.get("enable_bv_dedup", True):
        video_ids = bapi.filter_bv_dedup(bv_ids, int(config.get("bv_dedup_timeout", 60) or 60))
    else:
        video_ids = list(dict.fromkeys(bv_ids))
    if not video_ids:
        return

    # 5. 限制数量
    video_ids = video_ids[: int(config.get("max_parse_count", 3) or 3)]
    source = f"群{group_id}" if group_id else "私聊"
    logger.info(f"{source} 识别到 {len(video_ids)} 个视频: {video_ids}")

    # 6. 逐个查询视频信息并构建消息链
    chain: list = []
    if config.get("is_reply", True) and event.message_id:
        chain.append({"type": "reply", "data": {"id": event.message_id}})
    for i, vid in enumerate(video_ids):
        try:
            info = await bapi.get_video_info(
                vid,
                timeout=int(config.get("timeout", 10) or 10),
                cookie=config.get("cookie", "") or "",
            )
            if info:
                if i > 0:
                    chain.append({"type": "text", "data": {"text": "\n──────────────\n"}})
                chain.extend(bapi.build_video_message(info, config.get("show_cover", True)))
        except Exception as e:
            logger.error(f"解析 {vid} 失败: {e}")

    if len(chain) <= 1:  # 仅 reply 段 → 无解析结果
        return

    await event.bot.send_msg(
        message_type=event.message_type,
        user_id=event.user_id,
        group_id=event.group.group_id,
        message=chain,
    )

    # LLM 接管规则：解析回复已接管「链接」话题，默认跳过 LLM；
    # 唯一例外——群聊中用户 @ 了 bot（如「@bot 这视频讲了啥」），
    # 说明期望 LLM 参与对话，不跳过。
    if not event.is_at_me():
        event.llm.stop()


def _check_group(config, group_id: str) -> bool:
    """检查群组是否允许使用。"""
    mode = config.get("group_mode", "all")
    group_configs = config.get("group_configs", {}) or {}
    if mode == "all":
        return group_configs.get(group_id, {}).get("enabled", True)
    if mode == "none":
        return False
    return group_configs.get(group_id, {}).get("enabled", False)
