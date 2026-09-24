"""表情词表：把"想表达什么"翻译成 QQ 的 emoji_id，并把 id 命名成可读标签。

## 为什么需要它

QQ 表情回应的 ``emoji_id`` 是数字串（``66``/``128077``/``2600`` 这类），模型没有可靠的
映射关系，只能猜数字——猜错的代价是**贴错表情，而且 QQ 不支持撤回回应**。所以工具层
必须替它把"语义"翻译成"数字 id"。

## 两条来源，**观察优先**

1. **观察优先**（``observed``）：本群真实用过的 id（来自群聊记录里的表情事件）。
   这是最可靠的一手证据：那个 id 此刻确实能被贴出来。
2. **静态表**（``DEFAULT_TAGS``）：校准过的常见表情。**它的价值是给模型一个"入门的
   词表"，不是权威**；遇到不确定的 id 就必须靠真机校准。

## 校准方法（改静态表前先做这一步）

打开 ``napcat_tools_debug``，对一条真实消息依次贴几个候选 id，然后在群聊记录里
看 ``KIND_EMOJI`` 事件落下来的 ``emoji_id``——能成功且指向预期表情的那个才是对的。
没有校准过的条目不要凭印象补进来。

## 拒绝猜测

解析不到就**返回错误**（带上可用标签与建议），绝不"挑一个最接近的 id"——
贴错是不可逆的社交动作，宁可不贴。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

#: 静态标签表：``标签 → emoji_id``。key 用中文短词（模型更容易在语义上命中）。
#: 说明：这些 id 属于"常见 QQ 表情回应"的经验值，**上线前建议用真机校准一次**；
#: 校准时以群聊记录事件里的实际 emoji_id 为准（见模块 docstring）。
DEFAULT_TAGS: dict[str, str] = {
    "赞": "76",        # 大拇指
    "比心": "307",     # 比心/爱心
    "笑哭": "182",     # 笑哭
    "微笑": "14",      # 微笑
    "doge": "179",     # 狗头
    "吃瓜": "4",       # 围观
    "问号": "263",     # 疑问
    "无语": "302",     # 无语
    "惊恐": "26",      # 惊讶
    "加油": "311",     # 加油
}

#: emoji_id 的合法形态：纯数字（QQ 的表情 id 都是数字串；超表情是更大的数字）
_EMOJI_ID_RE = re.compile(r"^\d{1,12}$")

#: 标签文本归一：去空白/标点，便于"点个赞"这种口语命中
_TAG_NOISE_RE = re.compile(r"[\s,，。.!！?？、:：;；'\"“”‘’()（）\[\]【】]+")


def is_valid_emoji_id(value: Any) -> bool:
    """是不是一个形态合法的 emoji_id。"""
    return bool(_EMOJI_ID_RE.match(str(value or "").strip()))


def normalize_tag(text: Any) -> str:
    """把口语标签归一成词表 key（如"点个赞！"→"赞"）。"""
    raw = _TAG_NOISE_RE.sub("", str(text or "").strip())
    if raw in DEFAULT_TAGS:
        return raw
    # 口语兜底：包含关系（"点个赞" 含 "赞"）
    for tag in DEFAULT_TAGS:
        if tag and tag in raw:
            return tag
    return raw


def label_for(emoji_id: Any) -> str:
    """反查 id 的标签（查不到就返回原 id）：用于渲染"你贴的是 赞(76)"。"""
    target = str(emoji_id or "").strip()
    for tag, value in DEFAULT_TAGS.items():
        if value == target:
            return tag
    return target


def resolve(
    tag_or_id: Any,
    *,
    observed: Iterable[Any] | None = None,
) -> tuple[str, str]:
    """把 ``tag_or_id`` 解析成 ``(emoji_id, 来源)``。

    来源取值：``"explicit"``（调用方直接给了合法 id）/ ``"observed"``（本群用过的 id）/
    ``"table"``（静态表）。解析不出来时返回 ``("", "")``，由调用方决定如何报错。
    """
    raw = str(tag_or_id or "").strip()
    if not raw:
        return "", ""
    # 合法 id 直接用（"复用群里见过的那个"就是这条路径）
    if is_valid_emoji_id(raw):
        return raw, "explicit"
    tag = normalize_tag(raw)
    if not tag:
        return "", ""
    table_hit = DEFAULT_TAGS.get(tag)
    if not table_hit or not is_valid_emoji_id(table_hit):
        return "", ""
    # 观察优先：本群真的贴出来过的 id 比静态表可信（这里只影响"来源"标注）
    observed_ids = {str(x).strip() for x in (observed or []) if str(x or "").strip()}
    return table_hit, "observed" if table_hit in observed_ids else "table"


def available_tags(limit: int = 12) -> str:
    """可用的语义标签列表（用于拼错误信息）。"""
    tags = list(DEFAULT_TAGS)[: max(1, int(limit))]
    return " / ".join(tags)
