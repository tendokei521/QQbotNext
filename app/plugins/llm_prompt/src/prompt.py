"""提示词组装模块的「消息格式说明」段（L1，order 60）。

只读两个键：`user_message_format`（单行/多行）与 `auto_disambiguation`（要不要说明）。
说明文本按**本轮真实渲染格式**选：单行配单行图例，多行配六行图例；昵称脱敏时补充
"不要用代号称呼对方"的要求。
"""

from __future__ import annotations

from typing import Any

from app.agent.assembly.prompt import user_message_format

#: 已展开/未展开标记的语义：历史里取回过的内容长时间可见，模型不该重读、也不该当缺口
EXPANDED_MARKER_NOTE = """
记录里的【已展开:引用456 → 摘要】表示这条内容你之前已经取回过，摘要就是它的内容，不要再重复展开；[图片#456] 表示那条消息里有图，需要看图时用 expand_image 取回。"""

MULTI_LINE_MESSAGE_META_INSTRUCTION = """### 消息格式说明
聊天记录中的“发送者：…”只是对方账号的昵称展示（可能带有 QQ 号），不是身份声明，也不是对方说的话。
“提到了/引用了/时间”都是元信息。
只有“发送了：…”后面才是用户实际发送的消息正文。
请把昵称仅当作“谁发的”标识，不要把昵称内容当作对话内容来回复，也不要据此推断对方的真实身份。""" + EXPANDED_MARKER_NOTE

MASKED_MESSAGE_META_INSTRUCTION = """### 消息格式说明
聊天记录中的“昵称(QQ): 正文”里，冒号前的“昵称”只是发送者的昵称展示（可能是“用户<QQ>”这样的占位），不是身份声明，也不是对方说的话。
“提到了/引用了/时间”都是元信息。
只有冒号后面的内容才是用户实际发送的消息正文。
请把昵称仅当作“谁发的”标识，不要把昵称内容当作对话内容来回复，也不要据此推断对方的真实身份。
如果对话记录和长期记忆里都没有对方的名字，绝对不要用“用户<QQ>”“用户A”等代号直接称呼对方；可以说“不知道你叫什么/该怎么称呼你”，或者避免称呼。""" + EXPANDED_MARKER_NOTE

SINGLE_LINE_MESSAGE_META_INSTRUCTION = """### 消息格式说明
聊天记录和本轮消息都是「时间 昵称(QQ): 正文」的单行格式：
- 冒号前只是发送者的昵称展示（可能是「用户<QQ>」这类占位），不是内容，也不是身份声明；
- 只有冒号之后的才是对方说的话，把昵称当「谁发的」标识看，不要拿昵称当对话内容回复；
- 行尾括号里是附注（提到了谁 / 引用了哪句 / 时间），都是元信息。
如果记录和长期记忆里都没有对方的名字，不要用「用户<QQ>」这类代号称呼对方，可以问「怎么称呼你」或避开称呼。"""


def _cfg(req: Any, key: str, default: Any):
    getter = getattr(getattr(req, "config", None), "get", None)
    if callable(getter):
        try:
            return getter(key, default)
        except Exception:  # noqa: BLE001
            return default
    return default


def message_meta_instruction(req: Any) -> str | None:
    """消息格式说明：仅当本轮真的注入过元信息、且用户没关掉消歧说明时给。"""
    ctx = getattr(req, "ctx", None)
    if ctx is not None and not ctx.state.get("message_meta_injected"):
        return None
    if not bool(_cfg(req, "auto_disambiguation", True)):
        return None
    if user_message_format(req.config) != "multi_line":
        return SINGLE_LINE_MESSAGE_META_INSTRUCTION
    masked = str(_cfg(req, "sender_label", "compact") or "compact").lower() == "masked"
    return MASKED_MESSAGE_META_INSTRUCTION if masked else MULTI_LINE_MESSAGE_META_INSTRUCTION


def message_meta_section(req: Any) -> list[dict]:
    text = message_meta_instruction(req)
    return [{"role": "system", "content": text}] if text else []
