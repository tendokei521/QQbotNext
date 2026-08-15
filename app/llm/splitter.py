"""流式文本句子切分器。"""

from __future__ import annotations

SENTENCE_DELIMITERS = set("。！？!?；;\n")


def split_sentences(text: str, max_length: int = 50) -> tuple[list[str], str]:
    """把累积文本切成完整句子，返回 (sentences, remainder)。

    - 按中文/英文句末标点或换行切分；
    - 超过 max_length 仍无标点时强制硬切；
    - 空片段会被丢弃。
    """
    sentences: list[str] = []
    start = 0
    length = len(text)

    for i, ch in enumerate(text):
        if ch in SENTENCE_DELIMITERS or (i - start + 1) >= max_length:
            piece = text[start:i + 1].strip()
            if piece:
                sentences.append(piece)
            start = i + 1

    remainder = text[start:].strip()
    return sentences, remainder
