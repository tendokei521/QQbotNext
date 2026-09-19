"""H4b 历史图片按需取回测试。

约定：
- 历史里的图片渲染成 ``[图片#<消息id>]``（可展开标记），**不内联**（避免上下文膨胀）；
- 模型调用 ``expand_image`` 按 id 取回 → 图片排进"本轮待传"队列；
- 视觉模型：图片作为一条"补全材料"user 消息附在请求末尾（不塞进原用户发言）；
- 文本模型：只留文字说明，不发图片块（清洗兜底也不会让它 400）；
- 讨论焦点（本轮触发消息）的图片仍走既有直接同传路径，与此无关。
"""

from __future__ import annotations

import types

from app.llm import history_enrich
from app.llm.context_tools import _handle_expand_image, drain_round_images
from app.llm.group_context import format_online_history


class _Bot:
    def __init__(self, message=None, fetch_ok=True):
        self._message = message if message is not None else [
            {"type": "image", "data": {"url": "https://cdn.example/pic.png"}},
        ]
        self.fetch_ok = fetch_ok
        self.calls: list = []

    async def get_msg(self, message_id):
        self.calls.append(message_id)
        if not self.fetch_ok:
            return {"status": "failed", "retcode": 1404, "data": None}
        return {
            "status": "ok",
            "data": {
                "message_id": message_id,
                "sender": {"user_id": 20002, "nickname": "小明", "card": ""},
                "message": self._message,
            },
        }


def _ctx(bot, *, modalities=("text", "image"), store_dir=None):
    return types.SimpleNamespace(
        bot=bot,
        session_id="group_466",
        user_id="20002",
        group_id="466",
        extra={},
        runtime=types.SimpleNamespace(
            config={"image_max_mb": 10},
            provider_chain=lambda: [{"provider": "openai", "modalities": list(modalities)}],
        ),
    )


# ---------- 渲染：带 id 的可展开标记 ----------


def test_history_image_renders_with_message_id_marker():
    text = format_online_history([{
        "time": 1788342260, "message_id": 1003,
        "sender": {"user_id": 778, "nickname": "我"},
        "message": [{"type": "image", "data": {"url": "https://cdn.example/a.png"}}],
    }])
    assert text.endswith("[图片#1003]")


def test_history_image_without_id_keeps_plain_placeholder():
    text = format_online_history([{
        "time": 1788342260,
        "sender": {"user_id": 778, "nickname": "我"},
        "message": [{"type": "image", "data": {"url": "https://cdn.example/a.png"}}],
    }])
    assert text.endswith("[图片]")


# ---------- expand_image ----------


async def test_expand_image_queues_payloads_and_records(tmp_path, monkeypatch):
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    monkeypatch.setattr(history_enrich, "_STORE", None, raising=False)
    history_enrich._LEDGERS.clear()

    bot = _Bot()
    ctx = _ctx(bot)
    # 生产路径由 prepare_prompt 注入记账本；测试里手动补上以验证记账
    ctx.extra["expansion_ledger"] = history_enrich.ledger_for(778, "group_466")
    out = await _handle_expand_image(ctx, {"messages": ["1003"]})

    assert "已取回" in out
    assert bot.calls == [1003]
    images = drain_round_images(ctx)
    assert images == [{"kind": "url", "value": "https://cdn.example/pic.png"}]
    # 队列被清空（只传一次）
    assert drain_round_images(ctx) == []
    # 记账：请求收尾会写进补全登记
    assert history_enrich.commit(778, "group_466", trigger_message_id="1003") >= 1


async def test_expand_image_without_image_segment_reports_honestly(tmp_path, monkeypatch):
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    monkeypatch.setattr(history_enrich, "_STORE", None, raising=False)

    ctx = _ctx(_Bot(message=[{"type": "text", "data": {"text": "只有文字"}}]))
    out = await _handle_expand_image(ctx, {"messages": ["1003"]})
    assert "没有图片" in out
    assert drain_round_images(ctx) == []


async def test_expand_image_failure_is_reported_not_invented(tmp_path, monkeypatch):
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    monkeypatch.setattr(history_enrich, "_STORE", None, raising=False)

    ctx = _ctx(_Bot(fetch_ok=False))
    out = await _handle_expand_image(ctx, {"messages": ["1003"]})
    assert "没有图片" in out or "取不到" in out
    assert drain_round_images(ctx) == []


async def test_expand_image_requires_ids_and_bot():
    ctx = _ctx(_Bot())
    assert (await _handle_expand_image(ctx, {})).startswith("error:")
    ctx.bot = None
    assert (await _handle_expand_image(ctx, {"messages": ["1"]})).startswith("error:")


# ---------- 图片随请求传回 ----------


def _messages():
    return [{"role": "system", "content": "人设"}, {"role": "user", "content": "看看那张图"}]


def test_round_images_appended_for_vision_model():
    from app.llm.chat import _append_round_images

    ctx = _ctx(_Bot())
    ctx.extra["round_images"] = [{"kind": "url", "value": "https://cdn.example/pic.png"}]
    messages = _messages()
    _append_round_images(messages, ctx, ["text", "image"])

    assert len(messages) == 3
    content = messages[-1]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert content[1] == {"type": "image_url", "image_url": {"url": "https://cdn.example/pic.png"}}


def test_round_images_skipped_for_text_only_model():
    from app.llm.chat import _append_round_images

    ctx = _ctx(_Bot(), modalities=("text",))
    ctx.extra["round_images"] = [{"kind": "url", "value": "https://cdn.example/pic.png"}]
    messages = _messages()
    _append_round_images(messages, ctx, ["text"])

    # 文本模型：不追加图片消息（队列里的图也不会被发出去）
    assert len(messages) == 2
