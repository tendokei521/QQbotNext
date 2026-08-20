"""隐式蒸馏（P4）：从对话里沉淀“值得长期记住的用户事实”。

设计：
- 按「用户」分组蒸馏：一次调用处理某用户最近几句（避免多人混在一起无法归属）；
- 输出严格格式 ``重要度(0~1) 事实``，解析后经 重要度阈值 + owner 归属入库；
- 任何失败静默降级，不阻塞主流程（对齐 scheduler 容错风格）；
- 由 ``MemoryManager.maybe_consolidate`` 限频调度，本模块保持纯净可单测。
"""

from __future__ import annotations

import re
from typing import Any

from app.llm import logger

EXTRACT_PROMPT_TMPL = """你是记忆提取器。下面是一位用户（{speaker}）最近说的几句话。
请提炼其中「值得长期记住」的用户事实：个人偏好、身份、习惯、承诺、重要事件、约定等。

规则：
1. 只输出事实，一行一条，不要解释、不要序号、不要引号；
2. 每行格式：<重要度0~1> <事实>，例如：0.9 用户喜欢喝美式咖啡
3. 事实以“用户”或昵称为主语，保留关键限定（时间/对象）；
4. 主观闲聊、寒暄、一次性消息不要记；
5. 若没有值得记的，只输出：无

用户的话：
{lines}
"""


def render_extract_prompt(user_id: Any, texts: list[str], is_group: bool) -> str:
    speaker = f"群成员 QQ:{user_id}" if is_group else f"私聊用户 QQ:{user_id}"
    lines = "\n".join(f"- {t}" for t in texts if t and str(t).strip())
    return EXTRACT_PROMPT_TMPL.format(speaker=speaker, lines=lines)


def parse_facts(text: str, fallback_importance: float = 0.6) -> list[dict]:
    """解析提取器输出 → [{content, importance}]。行内重要性前缀 \u30080.8 内容 / 0.8|内容 / 0.8,内容。"""
    facts: list[dict] = []
    for raw in (text or "").splitlines():
        line = raw.strip().lstrip("-•*· ")
        if not line or line in ("无", "无。", "没有", "暂无", "None"):
            continue
        m = re.match(r"^(\d(?:\.\d+)?)\s*[\s|:：,，]+\s*(.+)$", line)
        if m:
            try:
                importance = float(m.group(1))
            except ValueError:
                importance = fallback_importance
            content = m.group(2).strip()
        else:
            importance = fallback_importance
            content = line
        content = content.strip(" \"'“”’‘")
        if len(content) >= 4:
            facts.append({
                "content": content,
                "importance": max(0.0, min(1.0, importance)),
            })
    return facts


async def extract_facts_for_user_async(
    runtime: Any,
    user_id: Any,
    texts: list[str],
    is_group: bool,
    *,
    model: str | None = None,
    max_tokens: int = 300,
    timeout: int = 30,
) -> list[dict]:
    """对单个用户最近几句做一次蒸馏请求；任何异常返回 []。"""
    if not texts:
        return []
    try:
        cfg = getattr(runtime, "config", None)
        chain = None
        if hasattr(runtime, "provider_chain"):
            chain = runtime.provider_chain()
        if not chain:
            if cfg is None or not (cfg.get("api_key", "") or ""):
                return []
            from app.llm.providers import get_provider

            chain = [dict(cfg.raw_config)]
        prompt_text = render_extract_prompt(user_id, texts, is_group)
        from app.llm.providers import chat_with_fallback

        resp = await chat_with_fallback(
            chain,
            [{"role": "user", "content": prompt_text}],
            model=model or (cfg.get("model", "deepseek-chat") if cfg else "deepseek-chat"),
            temperature=0.0,
            max_tokens=max_tokens,
            timeout=timeout,
        )
        if not resp or not resp.ok:
            return []
        return parse_facts(resp.text)
    except Exception as e:
        logger.add_info("Memory").debug(f"[记忆] 蒸馏请求异常（忽略）: {e}")
        return []
