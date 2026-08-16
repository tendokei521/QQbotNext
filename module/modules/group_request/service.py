"""群申请管理业务逻辑。

每个群在 ``group_request_configs`` 下保存一份独立配置，结构示例：

.. code-block:: python

    {
        "123456": {
            "join_request.enable": True,
            "join_request.text_type": "text_all_refuse",
            "join_request.whitelist_keywords": ["答案1,答案2"],
            "join_request.level_type": "level_all_ignore",
            "join_request.min_level": 0,
            "join_request.blacklist_users": ["10001"],
        }
    }
"""

from __future__ import annotations

import re

from app.core.logger import module_logger


async def dynamic_options(module, bot):
    """返回可编辑的群列表，作为动态下拉框选项。"""
    if bot is None:
        return {"options": []}
    resp = await bot.get_group_list()
    groups = (resp or {}).get("data", []) or []
    options = []
    for g in groups:
        gid = g.get("group_id")
        if gid:
            options.append({
                "value": str(gid),
                "label": f"{g.get('group_name', '')} ({gid})",
            })
    return {"options": options}


async def dynamic_fields(module, bot, value):
    """返回某个群的独立配置字段（与旧 groupmanager_pro 保持一致）。"""
    return {
        "fields": [
            {
                "key": "join_request.enable",
                "type": "boolean",
                "label": "启用加群审核",
                "description": "是否自动处理该群的加群请求",
                "default": False,
            },
            {
                "key": "join_request.text_type",
                "type": "select",
                "label": "关键词过滤规则",
                "default": "text_all_ignore",
                "options": [
                    {"value": "text_all_ignore", "label": "不满足条件时忽略"},
                    {"value": "text_all_refuse", "label": "不满足条件时拒绝"},
                ],
            },
            {
                "key": "join_request.whitelist_keywords",
                "type": "string_list",
                "label": "入群关键词",
                "description": "同时满足多个词请在同一行用逗号分隔",
                "default": [],
                "placeholder": "如：答案1,答案2",
            },
            {
                "key": "join_request.level_type",
                "type": "select",
                "label": "QQ 等级过滤规则",
                "default": "level_all_ignore",
                "options": [
                    {"value": "level_all_ignore", "label": "等级不足时直接忽略"},
                    {"value": "level_all_refuse", "label": "等级不足时直接拒绝"},
                    {"value": "level_error_refuse", "label": "等级不足且答案错误时才拒绝"},
                ],
            },
            {
                "key": "join_request.min_level",
                "type": "number",
                "label": "入群过滤等级",
                "default": 0,
                "min": 0,
            },
            {
                "key": "join_request.blacklist_users",
                "type": "string_list",
                "label": "黑名单用户",
                "description": "申请入群时自动拒绝",
                "default": [],
                "placeholder": "QQ号",
            },
        ]
    }


def _group_config(module, group_id: str) -> dict:
    configs = module.config.get("group_request_configs", {}) or {}
    return configs.get(str(group_id), {}) or {}


async def handle_group_request(module, event):
    logger = module_logger.add_info(f"#{module.bot_id}").add_info(module.name)

    if event.bot is None:
        return

    group_id = str(event.group_id or 0)
    cfg = _group_config(module, group_id)

    if not cfg.get("join_request.enable", False):
        logger.info(f"群 {group_id} 未启用加群审核，忽略")
        return

    # 黑名单直接拒绝
    blacklist = [
        str(x).strip()
        for x in cfg.get("join_request.blacklist_users", []) or []
        if str(x).strip()
    ]
    if str(event.user_id) in blacklist:
        logger.info(f"群 {group_id} 黑名单用户 {event.user_id} 申请入群，拒绝")
        await event.bot.set_group_add_request(
            flag=event.flag,
            approve=False,
            reason="黑名单用户",
        )
        return

    # 获取申请者 QQ 等级
    try:
        resp = await event.bot.get_stranger_info(event.user_id)
        user_info = (resp or {}).get("data", {}) or {}
        user_level = int(user_info.get("qqLevel") or user_info.get("level") or 0)
    except Exception as e:
        logger.error(f"获取用户 {event.user_id} 信息失败: {e}")
        return

    comment = event.comment or ""
    texts = cfg.get("join_request.whitelist_keywords", []) or []
    text_type = cfg.get("join_request.text_type", "text_all_ignore")
    level_type = cfg.get("join_request.level_type", "level_all_ignore")
    min_level = int(cfg.get("join_request.min_level", 0) or 0)

    # 关键词判断：同一行内用逗号分隔的词需全部出现在申请备注中
    text_approve = False
    for text in texts:
        words = re.split(r"\s*[，,]\s*", str(text))
        words = [w for w in words if w]
        if words and all(word in comment for word in words):
            text_approve = True
            break

    level_approve = user_level >= min_level

    # 决策优先级与旧插件一致：通过 > 文本拒绝 > 等级拒绝 > 特殊拒绝 > 忽略
    if text_approve and level_approve:
        result, reason = "approve", ""
    elif not text_approve and text_type == "text_all_refuse":
        result, reason = "refuse", "入群问题回答错误"
    elif not level_approve and level_type == "level_all_refuse":
        result, reason = "refuse", "等级不满足入群要求"
    elif not text_approve and not level_approve and level_type == "level_error_refuse":
        result, reason = "refuse", "入群答案错误且等级过低"
    elif not text_approve or not level_approve:
        reasons = []
        if not text_approve:
            reasons.append("问题回答错误")
        if not level_approve:
            reasons.append("等级不足")
        result, reason = "ignore", "且".join(reasons) + "（已忽略）"
    else:
        result, reason = "ignore", "未知错误"

    logger.info(
        f"群 {group_id} 入群申请响应：[文本={text_approve}，等级={level_approve}]"
        f"结果={result}，原因={reason}"
    )

    if result == "approve":
        await event.bot.set_group_add_request(flag=event.flag, approve=True, reason=reason)
    elif result == "refuse":
        await event.bot.set_group_add_request(flag=event.flag, approve=False, reason=reason)
