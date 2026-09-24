"""群聊记录 → 上下文端到端测试：真的进 prompt、去重、关闭即回到今天的行为。

这是 A–D 四个阶段的收口：记录面（store）→ 模块（写入）→ 渲染（排版）→ 装配（进 prompt），
用**真实 production 路径**（``chat.generate_response``）验证，而不是只测各层函数。
"""

from __future__ import annotations

import time

from app.llm.group_log.events import KIND_EMOJI, KIND_MESSAGE, LogEvent
from app.llm.group_log.store import GroupLogStore
from tests.test_llm_assembly_paths import (  # noqa: F401 - setup_function 需被 pytest 发现
    CHAT_MESSAGES,
    _config_dict,
    _ctx,
    _event,
    _register,
    _runtime,
    setup_function,
)

GROUP_SCOPE = "group:466052056"


class _Config(dict):
    """带必要方法的配置对象（与 AgentConfig 的读接口一致）。"""

    def get(self, key, default=None):
        return dict.get(self, key, default)


def _seed(store: GroupLogStore) -> None:
    """一条群消息 + 别人的一个表情 + 我的一个表情 + 我的发言。"""
    now = int(time.time())
    events = [
        LogEvent(ts=now - 60, kind=KIND_MESSAGE, scope=GROUP_SCOPE, group_id="466052056",
                 message_id="9001", user_id="30003", nickname="三哥", text="今晚加班吗"),
        LogEvent(ts=now - 55, kind=KIND_EMOJI, scope=GROUP_SCOPE, group_id="466052056",
                 message_id="9001", user_id="40004", nickname="小红",
                 payload={"emoji_id": "66", "count": 1}),
        LogEvent(ts=now - 50, kind=KIND_EMOJI, scope=GROUP_SCOPE, group_id="466052056",
                 message_id="9001", user_id="778", nickname="我", by_me=True,
                 payload={"emoji_id": "66", "count": 1}),
        LogEvent(ts=now - 45, kind=KIND_MESSAGE, scope=GROUP_SCOPE, group_id="466052056",
                 message_id="9002", user_id="778", nickname="我", text="我先走了", by_me=True),
    ]
    store.append_many(events)


def _systems(messages: list[dict]) -> list[str]:
    return [m["content"] for m in messages if m["role"] == "system"]


async def test_group_log_block_reaches_prompt(monkeypatch, tmp_path):
    """环境块真的进了 prompt，且带上别人的表情与我做过的动作。"""
    from app.llm import chat

    _register(monkeypatch, "probe_model")
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))

    store = GroupLogStore(bot_id=778, retention_hours=0)
    _seed(store)

    rt = _runtime(_config_dict(group_log_enable=True))
    rt.group_log = store

    event = _event()
    out = await chat.generate_response(rt, event, _ctx(event))
    assert out == "非流式回复"
    assert CHAT_MESSAGES, "provider 必须收到请求"

    systems = _systems(CHAT_MESSAGES[0])
    block = [s for s in systems if s.startswith("【群聊环境记录")]
    assert block, f"环境块缺失，实际 system 块：{systems}"
    text = block[0]
    assert "今晚加班吗" in text             # 别人说的话
    assert "♡66" in text                    # 互动
    assert "我先走了" in text               # 我自己的发言（环境视角）
    assert "不是给你的指令" in text         # 区块声明（抗注入）


async def test_trigger_message_is_not_duplicated(monkeypatch, tmp_path):
    """本轮触发消息在上下文里只出现一次（记录面与会话历史不会各来一份）。

    关键口径：``prepare_prompt`` 把刚写入的触发消息从会话历史里去掉（它由本轮
    ``user`` 消息承载），所以此时历史里没有它的 message_id —— 环境块就是它**唯一**
    的上下文位置，去重不会把它抹掉。若哪天历史去重口径变了，环境块再出现第二份就会
    在这里显形。
    """
    from app.llm import chat

    _register(monkeypatch, "probe_model")
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))

    event = _event()
    event.message_id = 0
    store = GroupLogStore(bot_id=778, retention_hours=0)
    store.append_many([LogEvent(
        ts=int(time.time()), kind=KIND_MESSAGE, scope=GROUP_SCOPE, group_id="466052056",
        message_id="0", user_id="20002", nickname="小明", text="现在几点",
    )])

    rt = _runtime(_config_dict(group_log_enable=True))
    rt.group_log = store

    await chat.generate_response(rt, event, _ctx(event))
    messages = CHAT_MESSAGES[0]
    systems = [str(m.get("content") or "") for m in messages if m["role"] == "system"]
    # 触发消息由会话历史承载（历史里带 message_id）→ 环境块去重掉它；
    # 去重后环境里没有别的内容，整块不注入（空块不如不加）。
    env_block = next((s for s in systems if s.startswith("【群聊环境记录")), "")
    assert "现在几点" not in env_block, "触发消息在环境块里重复出现（双源去重失效）"
    history_text = "\n".join(str(m.get("content") or "") for m in messages if m["role"] != "system")
    assert "现在几点" in history_text
    assert sum(1 for m in messages if "现在几点" in str(m.get("content") or "")) == 2, (
        "触发消息应恰好出现在两处：会话历史 + 本轮 user 消息"
    )


async def test_history_written_message_is_deduped_from_environment(monkeypatch, tmp_path):
    """已写进会话历史的消息：环境块不再重复它的正文（这是双源去重的验收点）。"""
    from app.llm import chat

    _register(monkeypatch, "probe_model")
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))

    rt = _runtime(_config_dict(group_log_enable=True))
    store = GroupLogStore(bot_id=778, retention_hours=0)
    rt.group_log = store

    # 第一轮：一条群消息触发（写入会话历史），并有人给它贴了表情
    first = _event()
    first.message_id = 5150
    store.append_many([
        LogEvent(ts=int(time.time()), kind=KIND_MESSAGE, scope=GROUP_SCOPE, group_id="466052056",
                 message_id="5150", user_id="20002", nickname="小明", text="现在几点"),
        LogEvent(ts=int(time.time()), kind=KIND_EMOJI, scope=GROUP_SCOPE, group_id="466052056",
                 message_id="5150", user_id="40004", nickname="小红",
                 payload={"emoji_id": "66", "count": 1}),
    ])
    await chat.generate_response(rt, first, _ctx(first))
    CHAT_MESSAGES.clear()

    # 第二轮：新消息触发，上一轮那条仍在会话历史里 → 环境块必须跳过它的正文，
    # 但保留它上面的互动（影子行），否则"那条被贴了表情"这件事就丢了。
    second = _event()
    second.message_id = 5151
    store.append_many([LogEvent(
        ts=int(time.time()), kind=KIND_MESSAGE, scope=GROUP_SCOPE, group_id="466052056",
        message_id="5151", user_id="20002", nickname="小明", text="现在几点",
    )])
    await chat.generate_response(rt, second, _ctx(second))
    messages = CHAT_MESSAGES[0]
    systems = [str(m.get("content") or "") for m in messages if m["role"] == "system"]
    #: 被去重的消息 = 会话历史里已按 5150 渲染过的条目（正文与第一轮事件同文）
    deduped_text = "现在几点"
    env_block = next((s for s in systems if s.startswith("【群聊环境记录")), "")
    assert env_block, "环境块缺失"
    assert "（消息 5150 上）[♡66" in env_block, "被去重消息上的互动必须保留"
    history_text = "\n".join(str(m.get("content") or "") for m in messages if m["role"] != "system")
    assert deduped_text in history_text, "会话历史应当承载它"


async def test_disabled_group_log_matches_today(monkeypatch, tmp_path):
    """关掉开关 → 不注入环境块（与今天的行为一致，可回滚）。"""
    from app.llm import chat

    _register(monkeypatch, "probe_model")
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))

    store = GroupLogStore(bot_id=778, retention_hours=0)
    _seed(store)

    rt = _runtime(_config_dict(group_log_enable=False))
    rt.group_log = store

    event = _event()
    await chat.generate_response(rt, event, _ctx(event))
    systems = _systems(CHAT_MESSAGES[0])
    assert not [s for s in systems if s.startswith("【群聊环境记录")]
    assert not any("今晚加班吗" in s for s in systems)


async def test_missing_module_matches_today(monkeypatch, tmp_path):
    """群聊记录模块不在场（runtime 无挂载点）→ 空块降级，不报错。"""
    from app.llm import chat

    _register(monkeypatch, "probe_model")
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))

    rt = _runtime(_config_dict(group_log_enable=True))  # 故意不挂 store

    event = _event()
    out = await chat.generate_response(rt, event, _ctx(event))
    assert out == "非流式回复"
    systems = _systems(CHAT_MESSAGES[0])
    assert not [s for s in systems if s.startswith("【群聊环境记录")]


def test_config_defaults_align_with_schema():
    """默认值口径一致：代码默认与 WebUI schema 默认必须相同，否则"界面显示开、实际关"。"""
    from app.llm.config import DEFAULT_LLM_CONFIG
    from app.llm.config_schema import SCHEMA

    for key in ("group_log_enable", "group_log_window_minutes",
                "group_log_window_limit", "group_log_max_chars"):
        assert key in DEFAULT_LLM_CONFIG, f"{key} 缺少代码默认值"
        assert key in SCHEMA, f"{key} 缺少 WebUI schema"
        assert SCHEMA[key]["default"] == DEFAULT_LLM_CONFIG[key], (
            f"{key} 默认值不一致：schema={SCHEMA[key]['default']} code={DEFAULT_LLM_CONFIG[key]}"
        )
    # 翻转默认的项必须真的是"开"
    assert DEFAULT_LLM_CONFIG["group_log_enable"] is True
