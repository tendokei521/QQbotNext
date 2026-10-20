"""H4b 历史图片按需取回测试。

约定：
- 历史里的图片渲染成 ``[图片#<消息id>]``（可展开标记），**不内联**（避免上下文膨胀）；
- 模型调用 ``expand_image`` 按 id 取回 → 图片排进"本轮待传"队列；
- 取回时**先落盘再内联**：队列里是本地字节（base64），不是会过期的 QQ 直链；
- 视觉模型：图片作为一条"补全材料"user 消息附在请求末尾（不塞进原用户发言）；
- 文本模型：只留文字说明，不发图片块（清洗兜底也不会让它 400）；
- 讨论焦点（本轮触发消息）的图片仍走既有直接同传路径，与此无关。
"""

from __future__ import annotations

import base64
import types

import pytest

from app.agent.session import enrich as history_enrich
from app.agent.media import store as image_media
from app.agent.media import image as image_mod
from app.agent.tools.context_tools import _handle_expand_image, drain_round_images
from app.agent.context.group import format_online_history

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 32
PNG_B64 = base64.b64encode(PNG_BYTES).decode()
#: 队列里"一张图"的标准形状：本地字节 + 本地指向
LOCAL_IMAGE = {
    "kind": "base64",
    "value": PNG_B64,
    "mime": "image/png",
    "media": {"sha": image_media.sha_of(PNG_BYTES), "mime": "image/png", "bytes": len(PNG_BYTES)},
}


@pytest.fixture(autouse=True)
def _offline_download(monkeypatch):
    """expand_image 现在会落盘：把下载换成固定 PNG 字节，测试不联网。"""

    async def _download(url, *, max_bytes, timeout=0):
        return PNG_BYTES, image_media.sniff_mime(PNG_BYTES)

    monkeypatch.setattr(image_mod, "_download_bytes", _download)
    return _download


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
        bot_id=778,
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
    assert images == [LOCAL_IMAGE]
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
    from app.agent.loop.chat import _append_round_images

    ctx = _ctx(_Bot())
    ctx.extra["round_images"] = [dict(LOCAL_IMAGE)]
    messages = _messages()
    _append_round_images(messages, ctx, ["text", "image"])

    assert len(messages) == 3
    content = messages[-1]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"] == f"data:image/png;base64,{PNG_B64}"


def test_round_images_skipped_for_text_only_model():
    from app.agent.loop.chat import _append_round_images

    ctx = _ctx(_Bot(), modalities=("text",))
    ctx.extra["round_images"] = [dict(LOCAL_IMAGE)]
    messages = _messages()
    _append_round_images(messages, ctx, ["text"])

    # 文本模型：不追加图片消息（队列里的图也不会被发出去）
    assert len(messages) == 2


# ==================== 工具轮中途取回的图片必须当轮送达 ====================


def test_round_images_appended_returns_true_when_queued():
    """返回值供非流式路径判断「要不要补问一轮」。"""
    from app.agent.tools.context_tools import append_round_images

    ctx = _ctx(_Bot())
    ctx.extra["round_images"] = [dict(LOCAL_IMAGE)]

    assert append_round_images(_messages(), ctx, ["text", "image"]) is True
    assert append_round_images(_messages(), ctx, ["text", "image"]) is False  # 队列已空


def test_round_images_drained_only_once():
    """补图轮不能反复追加同一张图（否则补问轮无终止条件）。"""
    from app.agent.tools.context_tools import append_round_images, drain_round_images

    ctx = _ctx(_Bot())
    ctx.extra["round_images"] = [dict(LOCAL_IMAGE)]
    messages = _messages()

    append_round_images(messages, ctx, ["text", "image"])

    assert len(messages) == 3
    assert drain_round_images(ctx) == []


async def test_expand_image_receipt_points_at_this_round_not_next_dialogue(tmp_path, monkeypatch):
    """回执措辞必须与实际时机一致。

    旧文案是「**下一轮对话里你会直接看到它**」，但当时没有任何代码把图片递出去：
    模型只能凭文字描述瞎猜，用户看到的是编造的画面。现在图片在本轮下一次请求就送到，
    回执也必须这么说（否则模型会在一轮之内重复取图，或干脆放弃描述）。
    """
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    monkeypatch.setattr(history_enrich, "_STORE", None, raising=False)
    history_enrich._LEDGERS.clear()

    bot = _Bot()
    ctx = _ctx(bot)
    ctx.extra["expansion_ledger"] = history_enrich.ledger_for(778, "group_466")

    result = await _handle_expand_image(ctx, {"messages": ["1003"]})

    assert "下一轮对话" not in result
    assert "本轮" in result
    assert len(drain_round_images(ctx)) == 1
