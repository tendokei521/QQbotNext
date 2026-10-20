"""触发器模块（框架级）：判断消息是否触发 LLM 响应（@ 或关键词）。"""

from typing import Dict


def _seg_type(seg) -> str:
    return seg.type if hasattr(seg, "type") else seg.get("type", "")


def _seg_data(seg) -> Dict:
    return seg.data if hasattr(seg, "data") else seg.get("data", {})


def is_at_me(message_data, self_id: str) -> bool:
    for msg in message_data:
        if _seg_type(msg) == "at":
            qq = _seg_data(msg).get("qq")
            if str(qq) == str(self_id):
                return True
    return False


def extract_text(message_data) -> str:
    texts = []
    for msg in message_data:
        if _seg_type(msg) == "text":
            texts.append(_seg_data(msg).get("text", ""))
    return "".join(texts)


def check_trigger(message_data, self_id: str, trigger_at: bool, trigger_keyword) -> tuple:
    """返回 (是否触发, 是否被@, 文本)。"""
    at_triggered = False
    if trigger_at:
        at_triggered = is_at_me(message_data, self_id)

    text = extract_text(message_data)
    keyword_triggered = False
    if trigger_keyword:
        if isinstance(trigger_keyword, list):
            for kw in trigger_keyword:
                if kw and kw in text:
                    keyword_triggered = True
                    break
        elif isinstance(trigger_keyword, str):
            if trigger_keyword and trigger_keyword in text:
                keyword_triggered = True

    return at_triggered or keyword_triggered, at_triggered, text
