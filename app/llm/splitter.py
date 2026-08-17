"""流式文本句子切分器。"""

from __future__ import annotations

import re

SENTENCE_DELIMITERS = set("。！？!?；;\n")

# 部分上游模型/中转会在正文前后输出 "rate." 这类杂散 token。
# 这里做防御性清理：只移除独立成句或紧跟在中文句末标点后的 "rate."，
# 避免误伤正常英文句子（如 "I rate."）。
_ARTIFACT_RE = re.compile(r"(?i)(?:^\s*|(?<=[。！？!?；;\n])\s*)rate\.(?=\s*(?:$|[\u4e00-\u9fff]))")


def strip_stream_artifacts(text: str) -> str:
    """清理流式输出中已知的上游杂散 token（目前只有 rate.）。"""
    if not text:
        return ""
    return _ARTIFACT_RE.sub("", text).strip()


def split_sentences(text: str, max_length: int = 50) -> tuple[list[str], str]:
    """把累积文本切成完整句子，返回 (sentences, remainder)。

    - 按中文/英文句末标点或换行切分；
    - 连续的句末标点（如 “？！”、“?!”）作为同一个断句单位，不会拆成多条；
    - 超过 max_length 仍无标点时强制硬切；
    - 空片段会被丢弃。
    """
    sentences: list[str] = []
    start = 0
    length = len(text)
    i = 0

    while i < length:
        ch = text[i]
        if ch in SENTENCE_DELIMITERS:
            # 把连续的句末标点合并到同一个句子结尾
            j = i
            while j < length and text[j] in SENTENCE_DELIMITERS:
                j += 1
            piece = text[start:j].strip()
            if piece:
                sentences.append(piece)
            start = j
            i = j
        else:
            if (i - start + 1) >= max_length:
                piece = text[start:i + 1].strip()
                if piece:
                    sentences.append(piece)
                start = i + 1
            i += 1

    remainder = text[start:].strip()
    return sentences, remainder
