"""AstrBot 对齐的消息组装（核心实现）。

对齐依据（均已逐字核对 `1/AstrBot-master` v4.27.2 源码）：

**system prompt 段落顺序**（自顶向下）
1. 安全模式段 `LLM_SAFETY_MODE_SYSTEM_PROMPT`（`astr_main_agent.py:1117`：最后执行但**前插**）
2. ChatUI GenUI 段（`astr_main_agent.py:510`）
3. Persona 段 `"\\n# Persona Instructions\\n\\n{prompt}\\n"`（`:534`）
4. Skills 段 `"\\n{...}\\n"`（`:569`）
5. Subagent router 段 `"\\n{...}\\n"`（`:656`）
6. Workspace EXTRA_PROMPT 段（`:422-428`）
7. local/sandbox 模式段（`:442` / `:1156-1221`）
8. 工具调用段 `"\\n{...}\\n"`（`:1669`，`req.func_tool` 非空才加）
9. live 模式段（`:1673`）
10. 联网引用段（`:1279`）

**用户消息 content 块顺序**（`provider/entities.py:188-261`）
正文（经 `prompt_prefix` 包裹）→ `extra_user_content_parts` → 图片 → 音频；
恰好一个文本块且无额外块/媒体时**降级为纯字符串**；无正文但有图/音时补 `[图片]`/`[音频]`。

**额外块来源顺序**：媒体附件占位（`:1420-1534`）→ `<Quoted Message>`（`:943-945`）→
`<system_reminder>`（`:984-988`）→ 知识库（`:295-299`）→ 群聊上下文（`group_chat_context.py:192-195`）。

**消息结构**（`tool_loop_agent_runner.py:308-323`）：历史 → 当前 user → system 插到第 0 位；
persona 的 `begin_dialogs` 前插到历史最前（`astr_main_agent.py:536`）。
"""

from __future__ import annotations

from app.core.logger import logger

import time
from datetime import datetime
from typing import Any

from .astrbot_prompts import (
    CHATUI_INLINE_GENUI_SYSTEM_PROMPT,
    FILE_ATTACHMENT_PLACEHOLDER,
    GROUP_HISTORY_AT,
    GROUP_HISTORY_DIRECTED_AT_YOU,
    GROUP_HISTORY_FOOTER,
    GROUP_HISTORY_HEADER,
    GROUP_HISTORY_IMAGE,
    GROUP_HISTORY_LINE_HEAD,
    IMAGE_ATTACHMENT_PLACEHOLDER,
    IMAGE_ONLY_PLACEHOLDER,
    LIVE_MODE_SYSTEM_PROMPT,
    LLM_SAFETY_MODE_SYSTEM_PROMPT,
    PERSONA_SECTION_TITLE,
    QUOTED_MESSAGE_SENDER_INFO,
    QUOTED_MESSAGE_TEMPLATE,
    SYSTEM_REMINDER_DATETIME,
    SYSTEM_REMINDER_GROUP_NAME,
    SYSTEM_REMINDER_IDENTITY,
    SYSTEM_REMINDER_TEMPLATE,
    SYSTEM_REMINDER_TIME_FORMAT,
    TOOL_CALL_PROMPT,
    TOOL_CALL_PROMPT_SKILLS_LIKE_MODE,
    UNSUPPORTED_IMAGE_PLACEHOLDER,
    WEEKDAY_NAMES,
    WORKSPACE_EXTRA_ENTRY,
    WORKSPACE_EXTRA_HEADER,
)

#: 群名缓存有效期（秒）：组装是同步的，群名必须在 pre_request 阶段预先取好
GROUP_NAME_TTL = 1800
#: 参与组装的历史角色
HISTORY_ROLES = ("user", "assistant", "system")


# ==================== 配置读取 ====================


def _cfg(module: Any, key: str, default: Any = None) -> Any:
    getter = getattr(getattr(module, "config", None), "get", None)
    if not callable(getter):
        return default
    try:
        return getter(key, default)
    except Exception:  # noqa: BLE001 —— 配置对象异常不该让整轮请求炸掉
        return default


def _flag(module: Any, key: str, default: bool) -> bool:
    return bool(_cfg(module, key, default))


def _int_cfg(module: Any, key: str, default: int) -> int:
    try:
        value = int(_cfg(module, key, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _service(module: Any, name: str) -> Any:
    services = getattr(getattr(module, "ctx", None), "services", None)
    return getattr(services, name, None)


def _user_context(req: Any) -> dict:
    """框架 enhance 预取的用户上下文（sender / mentioned / quote / quote_sender / sent_text）。"""
    state = getattr(getattr(req, "ctx", None), "state", None)
    info = state.get("user_context") if isinstance(state, dict) else None
    return info if isinstance(info, dict) else {}


def _session_mgr(req: Any) -> Any:
    return getattr(getattr(req, "runtime", None), "session_mgr", None)


# ==================== 消息段取值（dict / 对象两种形态都吃） ====================


def _segments(event: Any) -> list:
    segs = getattr(event, "message", None)
    return list(segs) if isinstance(segs, (list, tuple)) else []


def _seg_type(seg: Any) -> str:
    if isinstance(seg, dict):
        return str(seg.get("type") or "")
    return str(getattr(seg, "type", "") or "")


def _seg_data(seg: Any) -> dict:
    if isinstance(seg, dict):
        data = seg.get("data")
    else:
        data = getattr(seg, "data", None)
    return data if isinstance(data, dict) else {}


# ==================== system prompt ====================


def _persona_prompt(module: Any, req: Any) -> str:
    """人设提示词：优先配置里的角色预设，其次 Agent 配置的 system_prompt。"""
    preset_id = str(_cfg(module, "persona_preset_id", "") or "").strip()
    if preset_id:
        service = _service(module, "role_presets")
        if service is not None:
            try:
                preset = service.get_preset(preset_id)
            except Exception:  # noqa: BLE001 —— 预设读失败退回 Agent 配置，不阻断本轮
                preset = None
            if preset and str(preset.get("system_prompt") or "").strip():
                return str(preset["system_prompt"]).strip()
    cfg_getter = getattr(req, "cfg", None)
    return str(cfg_getter("system_prompt", "") or "").strip() if callable(cfg_getter) else ""


def _begin_dialogs(module: Any, req: Any) -> list[dict]:
    """角色预设的 `begin_dialogs` → 前插到历史最前（AstrBot `:536`）。"""
    if not _flag(module, "persona_begin_dialogs", True):
        return []
    preset_id = str(_cfg(module, "persona_preset_id", "") or "").strip()
    if not preset_id:
        return []
    service = _service(module, "role_presets")
    if service is None:
        return []
    try:
        preset = service.get_preset(preset_id)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[assembler] 163: 已忽略异常: {e}")
        return []
    dialogs = (preset or {}).get("begin_dialogs") or []
    result: list[dict] = []
    for item in dialogs:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").lower()
        content = str(item.get("content") or "")
        if role in ("user", "assistant") and content.strip():
            result.append({"role": role, "content": content})
    return result


def _tool_call_prompt(module: Any, req: Any) -> str:
    """工具调用段：仅本轮真的挂了工具时注入（AstrBot `:1647-1669`）。"""
    mode = str(_cfg(module, "tool_call_prompt_mode", "full") or "full").lower()
    if mode == "off" or not (getattr(req, "available_tools", None) or []):
        return ""
    if mode == "skills_like":
        return TOOL_CALL_PROMPT_SKILLS_LIKE_MODE
    return TOOL_CALL_PROMPT


def build_system_prompt(module: Any, req: Any) -> str:
    """按 AstrBot 的段落顺序拼出**一整条** system prompt。"""
    text = ""

    # 2. ChatUI GenUI 段（AstrBot 里 WebChat 默认开；本框架默认关）
    if _flag(module, "genui_enable", False):
        text += CHATUI_INLINE_GENUI_SYSTEM_PROMPT

    # 3. Persona 段
    persona = _persona_prompt(module, req)
    if persona:
        text += f"\n{PERSONA_SECTION_TITLE}\n\n{persona}\n"

    # 4. Skills 段（AstrBot 由 SKILL.md 清单生成；本框架是模块 @skill 说明块，
    #    这里按 AstrBot 的**位置与分隔符**原样注入，不伪造它的模板）
    skill_blocks = [str(b) for b in (getattr(req, "skill_blocks", None) or []) if str(b or "").strip()]
    if skill_blocks:
        section = "\n\n".join(skill_blocks)
        text += f"\n{section}\n"

    # 5. Subagent router 段
    router = str(_cfg(module, "router_system_prompt", "") or "").strip()
    if router:
        text += f"\n{router}\n"

    # 6. Workspace EXTRA_PROMPT 段（本框架无 workspace，用配置文本喂）
    extra = str(_cfg(module, "extra_prompt_text", "") or "").strip()
    if extra:
        entry = WORKSPACE_EXTRA_ENTRY.format(path=str(_cfg(module, "extra_prompt_path", "EXTRA_PROMPT.md")), extra=extra)
        text = f"{text}\n{WORKSPACE_EXTRA_HEADER}{entry}\n"

    # 7. local 模式段
    local = str(_cfg(module, "local_mode_prompt", "") or "").strip()
    if local:
        text += f"\n{local}\n"

    # 8. 工具调用段
    tool_prompt = _tool_call_prompt(module, req)
    if tool_prompt:
        text += f"\n{tool_prompt}\n"

    # 9. live 模式段
    if _flag(module, "live_mode", False):
        text += f"\n{LIVE_MODE_SYSTEM_PROMPT}\n"

    # 10. 联网引用段
    citation = str(_cfg(module, "web_search_citation_prompt", "") or "").strip()
    if citation:
        text += f"\n{citation}\n"

    # 1. 安全段：AstrBot 最后执行但用 f"{SAFETY}\n\n{原内容}" **前插**，故最终在最顶
    if _flag(module, "llm_safety_mode", True):
        text = f"{LLM_SAFETY_MODE_SYSTEM_PROMPT}\n\n{text}"
    return text


# ==================== 历史 ====================


def _raw_history(module: Any, req: Any) -> list[dict]:
    """AstrBot 风格历史：只有 role / content（+ tool_calls / tool_call_id）。

    框架存储里还带 `base`（原始段/发送者/时间）/`nickname`/`message_id` 等元信息——
    AstrBot 的 `contexts` 是 `Message.model_dump()`，**不会**把这些发出去，
    所以这里用 `history_snapshot()` 取原始条目再投影成 AstrBot 的形状。
    """
    session_mgr = _session_mgr(req)
    snapshot = getattr(session_mgr, "history_snapshot", None)
    if not callable(snapshot):
        return []
    try:
        entries = snapshot(getattr(req, "session_id", ""))
    except Exception as e:  # noqa: BLE001 —— 取不到历史就当作没有，不阻断本轮
        logger.debug(f"[assembler] 259: 已忽略异常: {e}")
        return []
    result: list[dict] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        role = str(entry.get("role") or "")
        if role not in HISTORY_ROLES:
            continue
        item: dict[str, Any] = {"role": role, "content": entry.get("content", "")}
        for key in ("tool_calls", "tool_call_id"):
            if entry.get(key) is not None:
                item[key] = entry[key]
        result.append(item)
    return result


def _history_messages(module: Any, req: Any) -> list[dict]:
    mode = str(_cfg(module, "history_mode", "raw") or "raw").lower()
    if mode == "off":
        return []
    if mode == "rendered":
        # 框架已渲染的形态（带「发送者：/发送了：」等元信息）
        return [dict(m) for m in (getattr(req, "history", None) or []) if isinstance(m, dict)]
    return _raw_history(module, req)


# ==================== 用户消息 ====================


def _apply_prompt_prefix(prefix: str, prompt: str) -> str:
    """`prompt_prefix` 的两种分支（AstrBot `astr_main_agent.py:361-368`）。"""
    if not prefix:
        return prompt
    if "{{prompt}}" in prefix:
        return prefix.replace("{{prompt}}", prompt)
    return f"{prefix}{prompt}"


def _attachment_placeholders(module: Any, req: Any) -> list[str]:
    """当前消息里的媒体 → 文本占位（AstrBot `:1420-1445`）。

    只复刻拿到过原文的两种（image / file）；音频、视频的占位文案上游未取到，不伪造。
    """
    if not _flag(module, "media_placeholder", True):
        return []
    result: list[str] = []
    for seg in _segments(getattr(req, "event", None)):
        kind = _seg_type(seg).lower()
        data = _seg_data(seg)
        path = str(data.get("file") or data.get("url") or "")
        if kind == "image":
            result.append(IMAGE_ATTACHMENT_PLACEHOLDER.format(path=path))
        elif kind == "file":
            name = str(data.get("name") or path)
            result.append(FILE_ATTACHMENT_PLACEHOLDER.format(name=name, path=path))
    return result


def _quote_block(module: Any, req: Any) -> str:
    """引用消息块（AstrBot `:943-945`；`sender_info` 为 `"({昵称}): "`）。"""
    if not _flag(module, "include_quoted_message", True):
        return ""
    info = _user_context(req)
    text = str(info.get("quote") or "").strip()
    if not text:
        return ""
    sender = str(info.get("quote_sender") or "").strip()
    head = QUOTED_MESSAGE_SENDER_INFO.format(nickname=sender) if sender else ""
    return QUOTED_MESSAGE_TEMPLATE.format(content=f"{head}{text}")


def _sender_identity(req: Any) -> tuple[str, str]:
    event = getattr(req, "event", None)
    user_id = str(getattr(event, "user_id", None) or getattr(req, "user_id", "") or "")
    user = getattr(event, "user", None)
    nickname = str(getattr(user, "card", "") or getattr(user, "nickname", "") or "")
    if not nickname:
        sender = str(_user_context(req).get("sender") or "")
        nickname = sender.split("(")[0] if "(" in sender else sender
    return user_id, nickname


def _group_name(module: Any, req: Any) -> str:
    """群名：由 `pre_request` 钩子预先取好放进模块缓存（组装是同步的）。"""
    group_id = str(getattr(req, "group_id", "") or "")
    if not group_id:
        return ""
    cache = getattr(module, "_group_names", None)
    if not isinstance(cache, dict):
        return ""
    hit = cache.get(group_id)
    if not isinstance(hit, tuple) or len(hit) != 2:
        return ""
    name, fetched_at = hit
    if (time.time() - float(fetched_at or 0)) > GROUP_NAME_TTL:
        return ""
    return str(name or "")


def _system_reminder(module: Any, req: Any) -> str:
    """`<system_reminder>` 块（AstrBot `:948-988`）。"""
    parts: list[str] = []
    if _flag(module, "identifier", False):
        user_id, nickname = _sender_identity(req)
        if user_id or nickname:
            parts.append(SYSTEM_REMINDER_IDENTITY.format(user_id=user_id, nickname=nickname))
    if _flag(module, "group_name_display", False) and getattr(req, "group_id", None):
        name = _group_name(module, req)
        if name:
            parts.append(SYSTEM_REMINDER_GROUP_NAME.format(group_name=name))
    if _flag(module, "datetime_system_prompt", True):
        now = datetime.now().astimezone()
        parts.append(
            SYSTEM_REMINDER_DATETIME.format(
                current_time=now.strftime(SYSTEM_REMINDER_TIME_FORMAT),
                weekday=WEEKDAY_NAMES[now.weekday()],
            )
        )
    if not parts:
        return ""
    return SYSTEM_REMINDER_TEMPLATE.format(content="\n".join(parts))


def _format_group_history_line(module: Any, req: Any, entry: dict) -> str:
    """AstrBot 群聊记录单条：`[昵称/HH:MM:SS]: ` + 各组件（每个组件前导一个空格）。

    与上游的刻意差异：时间取**该条消息自身**的时间戳（AstrBot 用的是注入时刻，
    会把所有行都写成当前时间）——框架历史里存着真实时间，用它信息量更大。
    """
    base = entry.get("base") if isinstance(entry.get("base"), dict) else {}
    nickname = str(base.get("sender_name") or entry.get("nickname") or "")
    raw_ts = base.get("time") or entry.get("time")
    try:
        stamp = time.strftime("%H:%M:%S", time.localtime(int(raw_ts)))
    except (TypeError, ValueError, OSError):
        stamp = datetime.now().strftime("%H:%M:%S")

    parts = [GROUP_HISTORY_LINE_HEAD.format(nickname=nickname, time=stamp)]
    self_ids = {
        str(getattr(req, "user_id", "") or ""),
        str(getattr(getattr(req, "runtime", None), "bot_id", "") or ""),
        str(getattr(getattr(req, "event", None), "self_id", "") or ""),
    }
    for seg in base.get("segments") or []:
        kind = _seg_type(seg).lower()
        data = _seg_data(seg)
        if kind == "text":
            parts.append(f" {data.get('text', '')}")
        elif kind == "image":
            parts.append(GROUP_HISTORY_IMAGE)
        elif kind in ("at", "mention"):
            qq = str(data.get("qq") or data.get("user_id") or "")
            if qq and qq in self_ids:
                parts.insert(1, GROUP_HISTORY_DIRECTED_AT_YOU)
            parts.append(GROUP_HISTORY_AT.format(name=str(data.get("name") or data.get("nickname") or qq)))
    return "".join(parts)


def _group_history_block(module: Any, req: Any) -> str:
    """群聊上下文块（AstrBot `group_chat_context.py:31-37` 的原文头尾）。"""
    if not _flag(module, "include_group_history", True):
        return ""
    if not getattr(req, "group_id", None):
        return ""
    session_mgr = _session_mgr(req)
    snapshot = getattr(session_mgr, "history_snapshot", None)
    if not callable(snapshot):
        return ""
    try:
        entries = snapshot(getattr(req, "session_id", ""))
    except Exception as e:  # noqa: BLE001
        from app.core.logger import logger
        logger.debug(f"[assembler] 430: 已忽略异常: {e}")
        return ""
    limit = _int_cfg(module, "group_history_max_messages", 50)
    lines = [
        _format_group_history_line(module, req, entry)
        for entry in (entries or [])[-limit:]
        if isinstance(entry, dict) and entry.get("role") == "user"
    ]
    if not lines:
        return ""
    return GROUP_HISTORY_HEADER + "\n".join(lines) + GROUP_HISTORY_FOOTER


def _image_blocks(req: Any) -> list[dict]:
    """图片 → OpenAI 多模态块（AstrBot 走 base64 data URL；框架已解析好，直接转）。"""
    blocks: list[dict] = []
    for image in getattr(req, "user_images", None) or []:
        if not isinstance(image, dict):
            continue
        kind = str(image.get("kind", "")).lower()
        value = str(image.get("value", "") or "").strip()
        if not value:
            continue
        if kind == "base64":
            mime = str(image.get("mime") or "image/png").strip() or "image/png"
            blocks.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{value}"}})
        elif kind == "url":
            blocks.append({"type": "image_url", "image_url": {"url": value}})
    return blocks


def _images_supported(req: Any) -> bool:
    from app.domain.modalities import supports_image

    return supports_image(getattr(req, "modalities", None))


def build_user_message(module: Any, req: Any) -> dict:
    """当前用户消息（`provider/entities.py:188-261` 的块顺序与降级规则）。"""
    raw_text = str(getattr(req, "raw_user_text", "") or "").strip() or str(getattr(req, "user_text", "") or "")
    prefix = str(_cfg(module, "prompt_prefix", "{{prompt}}") or "")
    text = _apply_prompt_prefix(prefix, raw_text)

    extra_parts: list[str] = []
    extra_parts.extend(_attachment_placeholders(module, req))
    for block in (_quote_block(module, req), _system_reminder(module, req), _group_history_block(module, req)):
        if block:
            extra_parts.append(block)

    images = _image_blocks(req)
    if images and not _images_supported(req):
        # 模型不支持图片：AstrBot 会清空 image_urls 并留一个 [Image] 文本块
        images = []
        extra_parts.append(UNSUPPORTED_IMAGE_PLACEHOLDER)

    blocks: list[dict] = []
    if text.strip():
        blocks.append({"type": "text", "text": text})
    elif images:
        blocks.append({"type": "text", "text": IMAGE_ONLY_PLACEHOLDER})
    blocks.extend({"type": "text", "text": part} for part in extra_parts)
    blocks.extend(images)

    # 降级：恰好一个文本块、且无额外块/无媒体 → 纯字符串（AstrBot 的向后兼容分支）
    if len(blocks) == 1 and blocks[0].get("type") == "text" and not extra_parts and not images:
        return {"role": "user", "content": blocks[0]["text"]}
    if not blocks:
        # 兜底：既无正文也无任何块，不该发生（prepare_prompt 已挡过），保持可发送
        return {"role": "user", "content": str(getattr(req, "user_text", "") or "")}
    return {"role": "user", "content": blocks}


# ==================== 总装 ====================


def build(module: Any, req: Any) -> list[dict]:
    """本轮 messages：`[system] + begin_dialogs + history + [user]`。

    与 AstrBot 的 `reset()` 一致——system 先生成、最后插到第 0 位；`begin_dialogs`
    在历史最前（`:536`）。
    """
    messages: list[dict] = []
    system_prompt = build_system_prompt(module, req)
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.extend(_begin_dialogs(module, req))
    messages.extend(_history_messages(module, req))
    messages.append(build_user_message(module, req))
    return messages
