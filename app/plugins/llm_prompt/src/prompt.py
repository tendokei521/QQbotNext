"""提示词组装模块自带的消息格式说明段（L1）。

## 为什么文案在这里

"聊天记录里的 `昵称(QQ): 正文` 到底哪部分是正文"是本模块渲染出来的事实（它持有
`include_*` / `meta_*` 这些渲染开关），说明文字必须跟着它走：模块禁用/卸载时
说明段一起消失，否则模型会拿着一本"解释不存在格式"的手册。

框架侧（`app/agent/assembly/prompt.py`）已不再持有这三份说明文案。
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


def message_meta_instruction(req: Any) -> str | None:
    """消息格式说明：让模型知道"昵称(QQ): 正文"里哪部分是正文。

    仅当本轮**真的注入了元信息**（`ctx.state["message_meta_injected"]`）时才给说明。
    """
    ctx = getattr(req, "ctx", None)
    if ctx is not None and not ctx.state.get("message_meta_injected"):
        return None
    cfg = getattr(req, "config", None)
    mode = None
    getter = getattr(cfg, "get", None)
    if callable(getter):
        try:
            mode = getter("meta_instruction_mode")
        except Exception:  # noqa: BLE001
            mode = None
    if mode is None:
        try:
            mode = getter("meta_sender_style", "legacy") if callable(getter) else "legacy"
        except Exception:  # noqa: BLE001
            mode = "legacy"
    mode = str(mode or "legacy").lower()
    if mode == "off":
        return None

    fmt = user_message_format(cfg)
    if fmt != "legacy":
        return SINGLE_LINE_MESSAGE_META_INSTRUCTION
    if mode == "new":
        return MASKED_MESSAGE_META_INSTRUCTION
    try:
        long_term = bool(getter("experimental_long_term_memory", False)) if callable(getter) else False
    except Exception:  # noqa: BLE001
        long_term = False
    return MASKED_MESSAGE_META_INSTRUCTION if long_term else MULTI_LINE_MESSAGE_META_INSTRUCTION


def message_meta_section(req: Any) -> list[dict]:
    text = message_meta_instruction(req)
    return [{"role": "system", "content": text}] if text else []
