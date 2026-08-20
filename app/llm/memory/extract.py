"""隐式蒸馏（v2 / S3）：从对话里「复述用户说过的话」，而非捏造。

改动要点：
- 只记录用户明确**说过/提到过**的内容；禁止把推断（动机/未来/他人看法/性格结论）写成记忆；
- 输出可带信度词 ``[很确定]/[好像]/[不确定]`` → 映射为记忆 confidence（0.65/0.55/0.45）；
- 行内含推断词 → 直接丢弃（防“看起来/应该会/可能要”之类的假记忆）。
"""

from __future__ import annotations

import re
from typing import Any

from app.llm import logger

EXTRACT_PROMPT_TMPL = """你是记忆提取器。下面是一位用户（{speaker}）最近说的几句话。

请提炼其中「值得长期记住」的内容。规则：
1. 只复述用户明确说过的内容（个人偏好、身份、习惯、住址、约定、明确事实）；
2. 禁止把推断当事实：不要写“用户看起来/可能/应该/预计会/将来要/似乎想”之类的猜测，
   动机、未来计划推测、他人看法一律不要记录；
3. 可给每条加置信语气：[很确定] / [好像] / [不确定]（默认按内容确定性自行标注）；
4. 一行一条，格式：<重要度0~1> [语气] <内容>，例如：
   0.9 [很确定] 用户说过喜欢喝美式咖啡
   0.6 [好像] 用户提到过想养一只猫
5. 若无值得记的，只输出：无

用户的话：
{lines}
"""

# 推断词黑名单：命中含这些词的行直接丢弃
_INFER_WORDS = (
    "看起来", "应该会", "可能会", "预计", "估计", "推测", "我猜", "似乎", "好像要",
    "将来要", "以后会", "说不定", "大概会", "一定想", "可能是", "本质上是", "个大概",
)

_CONF_MARK = {
    "很确定": 0.65,
    "确定": 0.65,
    "好像": 0.55,
    "不确定": 0.45,
}
_DEFAULT_CONF = 0.55


def render_extract_prompt(user_id: Any, texts: list[str], is_group: bool) -> str:
    speaker = f"群成员 QQ:{user_id}" if is_group else f"私聊用户 QQ:{user_id}"
    lines = "\n".join(f"- {t}" for t in texts if t and str(t).strip())
    return EXTRACT_PROMPT_TMPL.format(speaker=speaker, lines=lines)


def parse_facts(text: str, fallback_importance: float = 0.6) -> list[dict]:
    """解析提取器输出 → [{content, importance, confidence}]。

    支持前缀：``0.8 [很确定] 用户说过…`` / ``0.8 [好像] …`` / ``0.8 用户说过…``。
    含推断词的行直接丢弃。
    """
    facts: list[dict] = []
    for raw in (text or "").splitlines():
        line = raw.strip().lstrip("-•*· ")
        if not line or line in ("无", "无。", "没有", "暂无", "None"):
            continue
        # 重要度前缀
        m = re.match(r"^(\d(?:\.\d+)?)\s*[|:：,，]?\s*(.+)$", line)
        if m and m.group(1):
            try:
                importance = float(m.group(1))
            except ValueError:
                importance = fallback_importance
            rest = m.group(2).strip()
        else:
            importance = fallback_importance
            rest = line

        # 置信语气　→ confidence
        confidence = _DEFAULT_CONF
        cm = re.match(r"^[\[【（(]\s*([^\]】）)]+?)\s*[\]】）)][\s:：]*(.*)$", rest)
        if cm:
            tag = cm.group(1).strip()
            rest = cm.group(2).strip()
            # 长词在前，避免 “不确定” 被 “确定” 提前命中
            for key in ("不确定", "很确定", "好像", "确定"):
                if key in tag:
                    confidence = _CONF_MARK[key]
                    break
        else:
            # 正文含“好像/似乎/不太确定” → 降低置信
            if any(w in rest for w in ("好像", "似乎", "不确定", "可能")):
                confidence = min(confidence, 0.5)

        # 推断词 → 丢弃（防“看起来/应该会”这类假记忆）
        if any(w in rest for w in _INFER_WORDS):
            continue

        content = rest.strip(" \"'“”’‘")
        if len(content) >= 4:
            facts.append({
                "content": content,
                "importance": max(0.0, min(1.0, importance)),
                "confidence": max(0.0, min(1.0, confidence)),
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
            chain = [dict(cfg.raw_config)]
        from app.llm.providers import chat_with_fallback

        prompt_text = render_extract_prompt(user_id, texts, is_group)
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
