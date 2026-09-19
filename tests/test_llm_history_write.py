"""H2 写侧结构化测试：历史条目形状 = 基础信息 + 轮次信息 + message_id 锚点。

要点：
- 写历史时正文与「时间/群号/发送者」分离存（后者由渲染器按当前配置重排）；
- 保留原始消息段（供后续按需展开定位）；
- 保留 message_id（补全回写的锚点）；
- 旧调用（只传 content）仍写旧形状，不影响既有读取方。
"""

from __future__ import annotations

import types

from app.domain.events import GroupMessageEvent, MessageSegment, UserInfo
from app.llm.history_model import HistoryEntry, split_user_context
from app.llm.session import SessionManager

NL = chr(10)  # 显式换行，避免测试源码里被转义处理


def _mgr(tmp_path, bot_id=778) -> SessionManager:
    mgr = SessionManager(str(bot_id))
    mgr.history.history_dir = str(tmp_path / "history")
    return mgr


def test_add_message_structured_shape(tmp_path):
    mgr = _mgr(tmp_path)
    mgr.create_session("group_1", "group", 60)
    mgr.add_message(
        "group_1", "user", "学费怎么交", "1901691195", nickname="老师",
        message_id=889196000, timestamp=1788342260,
        segments=[{"type": "reply", "data": {"id": "456"}}, {"type": "text", "data": {"text": "学费怎么交"}}],
        group_id="466052056", is_private=False, meta_lines=["引用了：小明(20002)发送的引用消息：“学费”"],
    )

    entry = mgr.get_history("group_1")[0]
    base = entry["base"]
    assert base["text"] == "学费怎么交"
    assert base["sender_id"] == "1901691195"
    assert base["sender_name"] == "老师"
    assert base["group_id"] == "466052056"
    assert base["time"] == 1788342260
    assert [s["type"] for s in base["segments"]] == ["reply", "text"]
    assert base["meta_lines"] == ["引用了：小明(20002)发送的引用消息：“学费”"]
    assert entry["message_id"] == "889196000"
    # 兼容字段仍在（老读者/导出/记忆蒸馏不用改）
    assert entry["content"] == "学费怎么交"
    assert entry["user_id"] == "1901691195"


def test_add_message_legacy_shape_unchanged(tmp_path):
    mgr = _mgr(tmp_path)
    mgr.create_session("group_1", "group", 60)
    mgr.add_message("group_1", "assistant", "回复内容", timestamp=1788342260)

    entry = mgr.get_history("group_1")[0]
    assert entry == {"role": "assistant", "content": "回复内容", "time": 1788342260}


def test_get_history_preserves_turn_and_base_for_enrichment(tmp_path):
    """轮次信息必须能被读取侧看到，否则 H4 的补全回写无法按 message_id 定位。"""
    mgr = _mgr(tmp_path)
    mgr.create_session("group_1", "group", 60)
    mgr.add_message("group_1", "user", "看图", "20002", message_id=1001, segments=[{"type": "image", "data": {}}])
    session = mgr.get_session("group_1")
    session.data.history[0]["turn"] = {"expansions": [{"kind": "reply", "ref": "456", "summary": "学费"}]}

    entry = mgr.get_history("group_1")[0]
    assert entry["turn"]["expansions"][0]["summary"] == "学费"
    assert entry["base"]["segments"][0]["type"] == "image"


def test_structured_entry_renders_from_base_not_content(tmp_path):
    """渲染走 base：换行/发送者标签变化时不需要重写历史。"""
    from app.llm.group_context import format_history_for_llm

    mgr = _mgr(tmp_path)
    mgr.create_session("group_1", "group", 60)
    mgr.add_message(
        "group_1", "user", "裸正文", "20002", nickname="小明",
        timestamp=1788342260, segments=[{"type": "text", "data": {"text": "裸正文"}}],
    )
    rendered = format_history_for_llm(mgr.get_history("group_1"), is_private=False)
    assert rendered[0]["content"] == "09-02 17:44 小明(20002): 裸正文"
    # 结构化条目不应再被当成"增强块"原样输出
    assert not rendered[0]["content"].startswith("裸正文")


# ---------- 正文/元信息拆分 ----------


def test_split_user_context_separates_body_and_meta():
    text = NL.join([
        "(时间：2026-09-19 19:41:48)",
        "(当前群号: 466052056)",
        "发送者：老师(1901691195)",
        "提到了(用户名)：@小明(20002)",
        "引用了：小明(20002)发送的引用消息：“学费”",
        "发送了：学费怎么交",
    ])
    body, meta = split_user_context(text)
    assert body == "学费怎么交"
    assert meta == ["提到了(用户名)：@小明(20002)", "引用了：小明(20002)发送的引用消息：“学费”"]


def test_split_user_context_handles_plain_and_new_style():
    assert split_user_context("裸正文") == ("裸正文", [])
    body, meta = split_user_context(NL.join(["发送者昵称：小明(20002)", "消息正文：你好"]))
    assert (body, meta) == ("你好", [])


def test_rendered_entry_replays_meta_lines_after_body():
    from app.llm.history_model import BaseInfo, render_history_entry

    entry = HistoryEntry(
        role="user",
        base=BaseInfo(
            time=1788342260, sender_id="20002", sender_name="小明", text="看看",
            meta_lines=["提到了(用户名)：@小红(30003)"],
        ),
    )
    assert render_history_entry(entry) == (
        "09-02 17:44 小明(20002): 看看" + NL + "提到了(用户名)：@小红(30003)"
    )


# ---------- 与 chat 主路径的接线（端到端写入 → 下一轮渲染） ----------


async def test_chat_writes_then_renders_structured_history(monkeypatch, tmp_path):
    """端到端：第一轮写入结构化历史，第二轮请求里该历史按 base 重新渲染。"""
    from app.llm import chat
    from app.llm.providers import PROVIDERS
    from tests.test_llm_assembly_paths import (
        CHAT_MESSAGES,
        _ProbeProvider,
        _config_dict,
        _ctx,
        _runtime,
        setup_function,  # noqa: F401
    )

    monkeypatch.setitem(PROVIDERS, "probe_model", _ProbeProvider)
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    rt = _runtime(_config_dict())

    def _make_event(text: str, t: int, message_id: int):
        return GroupMessageEvent(
            event_type="message_group",
            message_type="group",
            time=t,
            message_id=message_id,
            user_id=20002,
            self_id=778,
            message=[MessageSegment("text", {"text": text})],
            user=UserInfo(user_id=20002, nickname="小明"),
            group=types.SimpleNamespace(group_id=466052056, group_name="测试群"),
        )

    first_body = "现在几点"
    second_body = "再问一次"

    # 第一轮：enhance 形态的 user_text（含元信息）→ 只把正文存进 base
    event1 = _make_event(first_body, 1788342260, 889196000)
    ctx1 = _ctx(event1)
    ctx1.user_text = NL.join(["发送者：小明(20002)", "发送了：" + first_body])
    await chat.generate_response(rt, event1, ctx1)

    # 第二轮：同一会话（时间推进避免冷却），观察历史如何渲染
    event2 = _make_event(second_body, 1788343000, 889196001)
    ctx2 = _ctx(event2)
    ctx2.user_text = NL.join(["发送者：小明(20002)", "发送了：" + second_body])
    await chat.generate_response(rt, event2, ctx2)

    sent = CHAT_MESSAGES[-1]
    history_lines = [
        m["content"] for m in sent
        if m["role"] == "user" and isinstance(m.get("content"), str)
    ]
    # 历史条目（非本轮）里第一轮正文以结构化单行形态出现，且带发送者标签与时间前缀
    history_only = [line for line in history_lines if second_body not in str(line)]
    assert any(": " + first_body in str(line) and "小明(20002)" in str(line) for line in history_only), history_lines
    # 历史里不再出现增强块原文（说明元信息没有冻进正文）
    assert not any(str(line).startswith("发送者：小明(20002)") for line in history_only), history_only
    # 本轮消息仍在最后一条
    assert second_body in str(sent[-1]["content"])
