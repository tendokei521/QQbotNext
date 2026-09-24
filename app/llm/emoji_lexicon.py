"""表情词表：把"想表达什么"翻译成 QQ 的 emoji_id，并把 id 命名成可读标签。

## 表从哪来（不再靠印象）

``SYSFACE_IDS``（``app/llm/qq_faces.py``）是**生成物**：QQ 客户端下发的系统表情表
``face_config.sysface``，由 ``scripts/export_napcat_faces.py`` 从本机 NapCat 包里导出。
它同时是 OneBot ``face`` 段的 ``id`` 空间与 ``set_msg_emoji_like`` 的 ``emoji_id``
空间，所以本模块的 ``DEFAULT_TAGS`` 是"全量映射"，不是十几个常见表情的摘录：

- 名字：客户端表里的 296 个表情名（微笑/撇嘴/呲牙/疑问/爱心/赞/比心/笑哭/doge/吃瓜/
  捂脸/沧桑/喵喵/666…）；
- 别名：``ALIASES`` 把口语词桥到表内正式名（点个赞→赞、狗头→doge、问号→疑问、
  无语→面无表情、加油→打call）。

## 网上列表有坑

流传最广的一份"QQ 表情代码"把 **微笑记成 1**；QQ 实际是 ``撇嘴=1、微笑=14、
爱心=66、赞=76``。照抄那份列表会贴错表情，而**表情回应不可撤回**。所以：

- 新增/修改表一律走 ``scripts/export_napcat_faces.py``（从客户端导出）；
- 客户端升级后跑 ``--check`` 核对，不要手抄任何列表。

## 两条来源，观察优先

1. **观察优先**（``observed``）：本群真实用过的 id（来自群聊记录里的表情事件）。
   这是最可靠的一手证据：那个 id 此刻确实能被贴出来。
2. **静态表**（``DEFAULT_TAGS``）：客户端表的快照 + 口语别名。它给模型一个可用的
   词表，但**大表情是已知的不确定项**（见下）。

## 大表情的已知不确定（``LARGE_FACES``）

``QSid >= 222`` 或带 ``AniStickerType`` 的表情（捂脸/吃瓜/比心/打call…）在客户端里走
``faceType 2/3``；表情回应是否同样吃这套 id 需要真机校准：开 ``onebot_tools_debug``，
对一条真实消息贴一次，看群聊记录里 ``KIND_EMOJI`` 落下来的 ``emoji_id``。
没校准过就说"不确定"，**不要凭印象补**。

## 拒绝猜测

解析不到就**返回错误**（带上可用标签与建议），绝不"挑一个最接近的 id"——
贴错是不可逆的社交动作，宁可不贴。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from app.llm.qq_faces import LARGE_FACES, SYSFACE_IDS

#: 口语别名 → 系统表情名（模型常说的词与客户端表里的正式名不一致时在此桥接）。
#: 只桥接"表里确实有的名字"，不凭印象造 id；**表内已有的名字一律按表走**
#: （例如「点赞」是客户端表里的独立表情 201，不被别名覆盖）。
ALIASES: dict[str, str] = {
    "点个赞": "赞",
    "狗头": "doge",
    "问号": "疑问",
    "无语": "面无表情",
    "加油": "打call",
    "打气": "打call",
    "强": "赞",
    "弱": "踩",
    "大兵": "悠闲",
    "ok": "OK",
    "no": "NO",
}


def _build_tags() -> dict[str, str]:
    """客户端系统表情全量名 + 口语别名（别名解析不到表内名字时跳过）。"""
    tags = dict(SYSFACE_IDS)
    for alias, name in ALIASES.items():
        emoji_id = SYSFACE_IDS.get(name, "")
        if emoji_id:
            tags[alias] = emoji_id
    return tags


def _build_reverse() -> dict[str, str]:
    """id → 规范名（同一 id 多个名字时取表内第一个，即 id 升序里的第一个）。"""
    reverse: dict[str, str] = {}
    for name, emoji_id in SYSFACE_IDS.items():
        reverse.setdefault(emoji_id, name)
    return reverse


#: 静态标签表：``标签 → emoji_id``。= 客户端系统表情全量名 + 口语别名。
DEFAULT_TAGS: dict[str, str] = _build_tags()

#: 大表情/动态表情的 id 集合（判形态用）
_LARGE_IDS: frozenset[str] = frozenset(
    emoji_id for name, emoji_id in SYSFACE_IDS.items() if name in LARGE_FACES
)

#: 表里存在的 id（用来把「666」这种"数字名字"和真 id 区分开）
_KNOWN_IDS: frozenset[str] = frozenset(SYSFACE_IDS.values())

#: 错误信息里优先展示的常用标签（顺序即展示顺序）
_COMMON_TAGS: tuple[str, ...] = (
    "赞", "比心", "笑哭", "doge", "吃瓜", "问号", "无语", "加油",
    "微笑", "爱心", "呲牙", "捂脸", "惊恐", "疑问", "666", "擦汗",
    "大哭", "偷笑", "沧桑", "喵喵",
)

#: emoji_id 的合法形态：纯数字（QQ 的表情 id 都是数字串；超表情是更大的数字）
_EMOJI_ID_RE = re.compile(r"^\d{1,12}$")

#: 标签文本归一：去空白/标点，便于"点个赞"这种口语命中
_TAG_NOISE_RE = re.compile(r"[\s,，。.!！?？、:：;；'\"“”‘’()（）\[\]【】]+")

#: id → 规范名（反查用）
_ID_TO_NAME: dict[str, str] = _build_reverse()


def is_valid_emoji_id(value: Any) -> bool:
    """是不是一个形态合法的 emoji_id。"""
    return bool(_EMOJI_ID_RE.match(str(value or "").strip()))


def is_large_face(value: Any) -> bool:
    """这个标签/id 是不是"大表情/动态表情"（回应的 id 形态待真机校准的那批）。

    接受标签名或 emoji_id；认不出时返回 ``False``。
    """
    raw = str(value or "").strip()
    if not raw:
        return False
    if raw in LARGE_FACES:
        return True
    if is_valid_emoji_id(raw):
        return raw in _LARGE_IDS
    return normalize_tag(raw) in LARGE_FACES


def normalize_tag(text: Any) -> str:
    """把口语标签归一成词表 key（如"点个赞！"→"赞"）。

    三级：精确命中 → 别名 → **多字标签**的包含匹配（长词优先）。
    单字标签（困/茶/哦/刀…）只认精确匹配，否则"很困难"会被当成贴「困」。
    """
    raw = _TAG_NOISE_RE.sub("", str(text or "").strip())
    if not raw:
        return ""
    if raw in DEFAULT_TAGS:
        return raw
    if raw in ALIASES:
        return ALIASES[raw]
    candidates = sorted(
        (tag for tag in DEFAULT_TAGS if len(tag) >= 2),
        key=len,
        reverse=True,
    )
    for tag in candidates:
        if tag in raw:
            return ALIASES.get(tag, tag)
    return raw


def label_for(emoji_id: Any) -> str:
    """反查 id 的规范名（查不到就返回原 id）：用于渲染"你贴的是 赞(76)"。"""
    target = str(emoji_id or "").strip()
    return _ID_TO_NAME.get(target, target)


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
    # 合法 id 直接用（"复用群里见过的那个"就是这条路径）。
    # 例外：客户端表里存在数字名字（如「666」），而这种数字又不是表里的 id 时，
    # 按名字解析——否则模型说 reaction="666" 会去贴一个不存在的 id。
    numeric_name = raw in DEFAULT_TAGS and raw not in _KNOWN_IDS
    if is_valid_emoji_id(raw) and not numeric_name:
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


def available_tags(limit: int = 20) -> str:
    """可用的语义标签采样（用于拼错误信息）：常用在前，不足用表内名字补齐。"""
    limit = max(1, int(limit))
    shown = list(_COMMON_TAGS[:limit])
    for tag in DEFAULT_TAGS:
        if len(shown) >= limit:
            break
        if tag not in shown:
            shown.append(tag)
    text = " / ".join(shown)
    if len(DEFAULT_TAGS) > len(shown):
        return f"{text}（共 {len(DEFAULT_TAGS)} 个）"
    return text
