"""llm_chat_v2 重构测试：prompt / provider / 会话-对话分离。"""

import asyncio

from app.llm.prompt import build_messages
from app.llm.providers import get_provider
from app.llm.providers.openai_compat import _split_keys
from app.llm import session as session_mod
from app.llm.history import HistoryManager


# ---------- prompt ----------

def test_build_messages_structure():
    messages = build_messages(
        system_prompt="你是助手",
        history=[{"role": "user", "content": "上一句"}],
        user_text="现在呢",
    )
    assert messages[0] == {"role": "system", "content": "你是助手"}
    # 定时任务协议为默认注入的系统消息
    assert messages[1]["role"] == "system"
    assert "schedule_task" in messages[1]["content"]
    assert messages[2] == {"role": "user", "content": "上一句"}
    assert messages[-1] == {"role": "user", "content": "现在呢"}


def test_build_messages_no_tags_no_history():
    m = build_messages(system_prompt="sys", user_text="hi", with_schedule_instruction=False)
    assert len(m) == 2
    assert m[1] == {"role": "user", "content": "hi"}


def test_strip_all_tags():
    """防御性标签剥离：任意 <type=xxx>（含 mood/action/中文）均剥离，不留空行。"""
    from app.llm.tags import strip_all_tags

    assert strip_all_tags("<type=mood>平静中带点无奈</type>\n<type=action>抬头看向来人</type>\n在啊") == "在啊"
    assert strip_all_tags("<type=posture>坐着</type>你好") == "你好"
    assert strip_all_tags("普通文本") == "普通文本"
    assert strip_all_tags("<type=中文>标签</type>ok") == "ok"
    assert strip_all_tags("") == ""


# ---------- provider（纯逻辑部分） ----------

def test_split_keys():
    assert _split_keys("") == []
    assert _split_keys("sk-a\nsk-b") == ["sk-a", "sk-b"]
    assert _split_keys("sk-a,sk-b，sk-c") == ["sk-a", "sk-b", "sk-c"]


def test_get_provider_unknown_type_falls_back():
    p = get_provider({"provider": "nonexistent", "api_key": "k"})
    from app.llm.providers.base import BaseProvider

    assert isinstance(p, BaseProvider)


async def test_provider_chat_without_key_returns_empty():
    p = get_provider({})  # 无 key → 立即失败，返回空响应（不发网络请求）
    resp = await p.chat([{"role": "user", "content": "hi"}])
    assert resp.ok is False


async def test_provider_tool_loop():
    """原生 function calling 工具循环：tool_calls → 执行 → 回传 → 最终回复。"""
    from app.llm.providers.openai_compat import OpenAICompatProvider

    provider = OpenAICompatProvider({"api_key": "sk-test"})
    calls = []

    async def _fake_request(payload, timeout):
        calls.append(payload["messages"])
        if len(calls) == 1:
            return {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "schedule_task", "arguments": '{"action":"create","note":"x"}'},
                        }],
                    },
                    "finish_reason": "tool_calls",
                }],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }
        return {
            "choices": [{"message": {"role": "assistant", "content": "好的，已安排。"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 8},
        }

    provider._request = _fake_request
    executed = []

    async def _executor(name, args):
        executed.append((name, args))
        return "success: 已创建"

    resp = await provider.chat(
        [{"role": "user", "content": "明早8点提醒我"}],
        tools=[{"type": "function", "function": {"name": "schedule_task", "description": "t", "parameters": {}}}],
        tool_executor=_executor,
    )

    assert resp.ok
    assert resp.text == "好的，已安排。"
    assert executed == [("schedule_task", {"action": "create", "note": "x"})]
    assert len(calls) == 2
    # 第二轮消息：assistant 保留 tool_calls，末尾追加 tool 结果
    second = calls[1]
    asst = [m for m in second if m["role"] == "assistant" and m.get("tool_calls")]
    assert asst and asst[0]["tool_calls"][0]["function"]["name"] == "schedule_task"
    assert second[-1] == {"role": "tool", "tool_call_id": "call_1", "content": "success: 已创建"}
    assert resp.tool_results[0]["name"] == "schedule_task"


# ---------- 会话 / 对话分离 ----------

def _mgr(tmp_path, bot_id):
    mgr = session_mod.SessionManager(bot_id)
    mgr.history.history_dir = str(tmp_path)
    return mgr


def test_session_multi_conversation(tmp_path):
    mgr = _mgr(tmp_path, "bot_a")
    mgr.create_session("group_1", "group", 60)
    mgr.add_message("group_1", "user", "第一条线你好")
    assert mgr.get_history("group_1")[0]["content"] == "第一条线你好"

    created = mgr.new_conversation("group_1", "第二对话")
    assert created["title"] == "第二对话"
    mgr.add_message("group_1", "user", "第二条线")
    # 活跃对话的历史隔离
    hist = mgr.get_history("group_1")
    assert len(hist) == 1 and hist[0]["content"] == "第二条线"
    assert mgr.get_session("group_1").active.title == "第二对话"

    # 切回第一个对话
    first_conv = list(mgr.get_session("group_1").conversations.values())[0]
    assert mgr.switch_conversation("group_1", first_conv.id) is True
    assert mgr.get_history("group_1")[0]["content"] == "第一条线你好"


def test_delete_conversation_keeps_at_least_one(tmp_path):
    mgr = _mgr(tmp_path, "bot_a")
    mgr.create_session("group_2", "group", 60)
    conv = mgr.get_session("group_2").active
    assert mgr.delete_conversation("group_2", conv.id) is False  # 唯一对话不可删
    mgr.new_conversation("group_2", "第二个")
    assert len(mgr.get_session("group_2").conversations) == 2
    assert mgr.delete_conversation("group_2", conv.id) is True
    assert len(mgr.get_session("group_2").conversations) == 1


def test_save_restore_roundtrip(tmp_path):
    mgr = _mgr(tmp_path, "bot_save")
    mgr.create_session("group_3", "group", 60)
    mgr.add_message("group_3", "user", "你好")
    mgr.add_message("group_3", "assistant", "你好呀")
    mgr.new_conversation("group_3", "第二")
    mgr.add_message("group_3", "user", "第二条")
    mgr.history.save_session(mgr.get_session("group_3"))
    mgr.stop_cleanup()

    # 新 manager（独立 bot_id，绕过单例）从归档恢复
    mgr2 = _mgr(tmp_path, "bot_restore")
    s = mgr2.create_session("group_3", "group", 60)
    mgr2.restore_session_from_archive(s, "group_3")
    assert len(s.conversations) == 2
    assert s.active.title == "第二"  # 最近对话设为活跃
    assert s.data.history[0]["content"] == "第二条"
    mgr2.stop_cleanup()


def test_session_manager_stats(tmp_path):
    mgr = _mgr(tmp_path, "bot_stats")
    mgr.create_session("group_1", "group", 60)
    mgr.create_session("private_9", "private", 60)
    stats = mgr.get_stats()
    assert stats["active"] == 2
    assert stats["groups"] == 1 and stats["privates"] == 1
    mgr.stop_cleanup()
