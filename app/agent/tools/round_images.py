"""轮次图片：把工具轮里产生的图片带到下一轮，并在合适时机取出。

## 本模块只负责

`append_round_images`（把本轮图片挂到 messages 上）、`drain_round_images`（取出并清空
队列）、以及配套的大小上限与记录。

## 为什么单独成文件

「图片要跟到下一轮」是一条独立的机制：有队列、有上限、有清理时机，却混在 1,000 行的
上下文工具里。`test_llm_history_image_expand` / `test_llm_round_images_delivery`
覆盖的就是它。
"""

from __future__ import annotations

"""按需展开上下文的系统级工具（``expand_recent`` / ``expand_message`` / ``expand_user``）。

定位：把渲染层标出来的「骨架」补成「血肉」。渲染层已经尽力预展开（@ 昵称、触发消息的
引用），但仍有三类内容只能按需取：更早/被引用的消息正文、合并转发内容、某个 QQ 是谁。

按**意图分区**拆成三个工具（而不是一个通用大工具）——通用工具只能写"出现未展开标记时调用"
这种抽象触发条件，模型无从判断该用哪个；分区后每个工具的 description 都能写清"何时必须调用"：

- ``expand_recent``：按**位置**取（"上一条/刚才那条/消息里的"）——不需要 id；
- ``expand_message``：按 **id** 取消息正文（引用 / 合并转发）；
- ``expand_user``：按 **QQ** 取身份（昵称/群名片/角色）。

设计要点（照抄 ``session_tools`` 的既有约定）：
- **支持批量**：可一次传多个 id/QQ，内部并发执行——串行会把墙钟时间叠加到超时；
- **自解释返回**：每条结果都带 ``relation``（"当前消息 @ 的对象" / "当前消息引用的消息"）；
- **登记优先**：本会话取回过的 id 直接复用摘要（零 API 调用），不再"重读取"；
- **如实汇报**：只取到发送者、正文仍是占位时归入"部分信息"，绝不让模型以为拿到了内容；
- **只承诺做得到的事**：图片/语音无法转文字不在此工具能力范围内。
"""


from app.core.logger import logger

# 内容截断策略：**默认不截断**（0 = 不限制）。
# 这些工具的目的就是"把骨架补成血肉"，截断等于把血肉又削掉一块（实测出现过
# 「能花那么多时」这种砍在词中间的摘要）。需要限长时由调用方显式传 ``limit``。


def _session_entries(ctx) -> list:
    """当前会话的历史条目（只读用途：复用本地图片指向）。取不到按空处理。"""
    try:
        from app.agent.session.manager import SessionManager

        session_id = str(getattr(ctx, "session_id", "") or "")
        if not session_id:
            return []
        bot_id = getattr(ctx, "bot_id", "") or getattr(getattr(ctx, "bot", None), "bot_id", "") or getattr(
            getattr(ctx, "runtime", None), "bot_id", ""
        )
        mgr = SessionManager(str(bot_id))
        return mgr.get_history(session_id, limit=mgr.MAX_HISTORY_MESSAGES)
    except Exception as e:  # noqa: BLE001 —— 拿不到历史就当没有本地指向
        logger.debug(f"[ExpandImage] 读取会话历史失败（按无缓存处理）: {e}")
        return []


def append_round_images(messages: list[dict], tool_ctx, modalities) -> bool:
    """把 ``expand_image`` 本轮取回的图片追加为一条 user 消息（仅视觉模型）。

    图片不塞进原用户消息（那是历史内容，不是本轮发言），而是作为"补全材料"附在末尾；
    文本模型跳过（图片块会被清洗成 ``[Image]``，没有意义）。

    放在这里而不是 ``chat``：主动消息 / 定时任务的流式工具循环也要用，而
    ``initiative_stream`` 不能 import ``chat``（循环导入）。返回是否真的追加了内容，
    非流式路径靠它决定"要不要补问一轮"。
    """
    if tool_ctx is None:
        return False
    try:
        from app.agent.media.image import build_user_content
        from app.domain.modalities import supports_image

        images = drain_round_images(tool_ctx)
    except Exception as e:  # noqa: BLE001 —— 补图是增强，失败不该影响主流程
        logger.debug(f"[ExpandImage] 取待传图片失败（已忽略）: {e}")
        return False
    if not images or not supports_image(modalities):
        return False
    content = build_user_content(images, text="（以下是你刚取回的历史图片）")
    if content:
        messages.append({"role": "user", "content": content})
        return True
    return False


def _runtime_config(ctx):
    """取运行时的 Agent 配置（缺失返回 None，调用方各自回默认值）。"""
    return getattr(getattr(ctx, "runtime", None), "config", None)


def _max_image_bytes(ctx) -> int:
    from app.agent.media.image import DEFAULT_MAX_IMAGE_BYTES, max_image_bytes

    config = _runtime_config(ctx)
    if config is None or not hasattr(config, "get"):
        return DEFAULT_MAX_IMAGE_BYTES
    try:
        return max_image_bytes(config)
    except Exception:  # noqa: BLE001
        return DEFAULT_MAX_IMAGE_BYTES


def _extra(ctx) -> dict:
    """取 ToolContext.extra；缺失时补一个（注意不能写 ``or {}``，空 dict 会被丢掉）。"""
    extra = getattr(ctx, "extra", None)
    if not isinstance(extra, dict):
        extra = {}
        try:
            ctx.extra = extra
        except Exception as e:  # noqa: BLE001 —— 只读 ctx 时不至于崩
            logger.debug(f"[Expand] 无法写入 ToolContext.extra: {e}")
    return extra


def _record_round_images(ctx, images: list[dict]) -> None:
    """把"本轮要随请求传回的图片"记到 ToolContext.extra（由 chat 落地）。"""
    try:
        bucket = _extra(ctx).setdefault("round_images", [])
        for image in images:
            if isinstance(image, dict) and image.get("value") and image not in bucket:
                bucket.append(dict(image))
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[ExpandImage] 记录待传图片失败（已忽略）: {e}")


def drain_round_images(ctx) -> list[dict]:
    """取出并清空"本轮待随请求传回"的图片（tool_ctx 可能为 None）。"""
    extra = getattr(ctx, "extra", None)
    if not isinstance(extra, dict):
        return []
    images = extra.pop("round_images", []) or []
    return [i for i in images if isinstance(i, dict) and i.get("value")]
