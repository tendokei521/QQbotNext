"""Emoji 回复业务逻辑。

- handle_emoji_notice：群消息被添加 Emoji 时，按概率跟随同一个 Emoji；
- handle_message：消息文本命中关键词时，按概率给该消息添加配置的 Emoji 回应；
- 使用缓存按 (bot, message_id, emoji_id) 做幂等；冷却只针对同一消息上的同一 Emoji，
  同一消息的不同 Emoji 仍可正常跟随/发送。
"""

from __future__ import annotations

import random
import re

from app.core.logger import module_logger

# 冷却 key 前缀：只按“同一消息 + 同一 Emoji”做幂等，不阻塞同一消息上的其它 Emoji
_COOLDOWN_EMOJI = "emoji_reply:emoji:{bot_id}:{message_id}:{emoji_id}"

# 关键词匹配用：移除标点、符号与空白，保留中文/字母/数字
_SYMBOL_RE = re.compile(r"[\W_]+")


def _logger(module):
    return module_logger.add_info(f"#{module.bot_id}").add_info(module.name)


def clean_symbols(text: str) -> str:
    """移除标点、符号与空白，保留中文/字母/数字（应对关键词间有符号）。"""
    return _SYMBOL_RE.sub("", text or "")


def parse_keyword_emoji_list(items) -> list[tuple[str, str]]:
    """解析 ['xxx:id', ...] 为 [(关键词, emoji_id), ...]，非法项跳过。"""
    result: list[tuple[str, str]] = []
    for item in items or []:
        text = str(item or "").strip()
        if not text or ":" not in text:
            continue
        keyword, _, emoji_id = text.partition(":")
        keyword = keyword.strip()
        emoji_id = emoji_id.strip()
        if keyword and emoji_id:
            result.append((keyword, emoji_id))
    return result


async def handle_emoji_notice(module, event) -> None:
    """跟随群消息上被添加的 Emoji。"""
    if not module.config.get("follow_emoji", True):
        return
    if not event.emoji_is_add:
        return

    message_id = getattr(event, "message_id", 0) or 0
    if not message_id:
        return

    cache = module.ctx.services.cache
    cooldown = int(module.config.get("cooldown_seconds", 60) or 0)
    prob = float(module.config.get("follow_emoji_prob", 0.5) or 0)
    log = _logger(module)

    for emoji in event.emoji_likes or []:
        if not isinstance(emoji, dict):
            continue
        emoji_id = str(emoji.get("emoji_id", "") or "").strip()
        if not emoji_id:
            continue

        emoji_key = _COOLDOWN_EMOJI.format(
            bot_id=module.bot_id, message_id=message_id, emoji_id=emoji_id
        )
        if cooldown > 0 and cache.has(emoji_key):
            continue
        if random.random() >= prob:
            continue

        # 先标记再发送，避免并发/重复上报导致同一 Emoji 被重复跟随
        if cooldown > 0:
            cache.set(emoji_key, True, cooldown)
        try:
            await event.bot.set_msg_emoji_like(message_id, emoji_id)
            log.debug(f"跟随 Emoji {emoji_id} on msg {message_id}")
        except Exception as e:
            log.error(f"跟随 Emoji {emoji_id} 失败: {e}")


async def handle_message(module, event) -> None:
    """按消息关键词发送 Emoji 回应。"""
    if not module.config.get("keyword_follow_enable", True):
        return

    message_id = getattr(event, "message_id", 0) or 0
    if not message_id:
        return

    cache = module.ctx.services.cache
    cooldown = int(module.config.get("cooldown_seconds", 60) or 0)
    prob = float(module.config.get("keyword_follow_prob", 0.5) or 0)
    log = _logger(module)

    keyword_emojis = parse_keyword_emoji_list(module.config.get("keyword_emoji_list", []))
    if not keyword_emojis:
        return

    clean_enabled = bool(module.config.get("keyword_symbol_clean", False))
    text = event.text or ""
    if clean_enabled:
        text = clean_symbols(text)

    for keyword, emoji_id in keyword_emojis:
        match_keyword = clean_symbols(keyword) if clean_enabled else keyword
        if not match_keyword or match_keyword not in text:
            continue
        if random.random() >= prob:
            continue

        emoji_key = _COOLDOWN_EMOJI.format(
            bot_id=module.bot_id, message_id=message_id, emoji_id=emoji_id
        )
        if cooldown > 0 and cache.has(emoji_key):
            continue
        if cooldown > 0:
            cache.set(emoji_key, True, cooldown)

        try:
            await event.bot.set_msg_emoji_like(message_id, emoji_id)
            log.debug(f"关键词 {keyword} → Emoji {emoji_id} on msg {message_id}")
        except Exception as e:
            log.error(f"关键词 Emoji {emoji_id} 发送失败: {e}")
