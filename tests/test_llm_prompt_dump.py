"""请求 messages 落盘通道（迁移对账：与第三方框架逐条 diff 的证据）。

日志里的 ``[Prompt]`` 是给人看的（截断/格式/多行），不能拿来做 diff。这里锁住
``prompt_dump`` 的行为：

1. **默认关闭**时零 IO（不建文件、不产生开销）；
2. 开启后每轮最终 messages 原样落 JSONL（会话/类型/条数可归类）；
3. 滚动保留最近 N 条；损坏行不毁整个文件；
4. 任何异常都不向上抛（辅助通道不能反过来影响对话）；
5. 与 ``apply_post_assemble`` 打通：四条组装路径统一收口，开启即自动落盘。
"""

from __future__ import annotations

import json

from app.llm import prompt_dump
from app.llm.assembly import PromptRequest, apply_post_assemble
from app.llm.hooks import LlmHookRegistry


class _Config:
    def __init__(self, data=None):
        self.data = dict(data or {})

    def get(self, key, default=None):
        return self.data.get(key, default)


class _Runtime:
    bot_id = 4242

    def __init__(self, config=None):
        self.config = _Config(config or {})
        self.llm_hooks = LlmHookRegistry()


def _messages(text="你好"):
    return [{"role": "system", "content": "sys"}, {"role": "user", "content": text}]


# ==================== 默认关闭 ====================


def test_disabled_by_default_writes_nothing(tmp_path):
    runtime = _Runtime()
    assert prompt_dump.record(runtime, _messages()) is False
    assert not (tmp_path / "llm_data").exists() or not prompt_dump.load_recent(runtime.bot_id)


def test_config_without_get_is_treated_as_disabled():
    class _NoGet:
        pass

    runtime = _Runtime()
    runtime.config = _NoGet()
    assert prompt_dump.enabled(runtime.config) is False
    assert prompt_dump.record(runtime, _messages()) is False


# ==================== 开启后落盘 ====================


def test_record_writes_entry_with_metadata():
    runtime = _Runtime({"prompt_dump_enable": True})
    assert prompt_dump.record(runtime, _messages("甲"), session_id="group_1", kind="chat") is True

    entries = prompt_dump.load_recent(runtime.bot_id)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["bot_id"] == runtime.bot_id
    assert entry["session_id"] == "group_1"
    assert entry["kind"] == "chat"
    assert entry["count"] == 2
    assert entry["messages"] == _messages("甲")
    assert entry["ts"] > 0


def test_load_recent_filters_by_session_and_limit():
    runtime = _Runtime({"prompt_dump_enable": True})
    for text in ["a1", "b1", "a2", "b2"]:
        prompt_dump.record(
            runtime,
            _messages(text),
            session_id="group_a" if text.startswith("a") else "group_b",
        )

    rows = prompt_dump.load_recent(runtime.bot_id)
    assert [r["messages"][1]["content"] for r in rows] == ["a1", "b1", "a2", "b2"], "旧 → 新"

    rows_a = prompt_dump.load_recent(runtime.bot_id, session_id="group_a")
    assert [r["messages"][1]["content"] for r in rows_a] == ["a1", "a2"]

    assert len(prompt_dump.load_recent(runtime.bot_id, limit=2)) == 2


def test_rolling_keeps_last_entries():
    runtime = _Runtime({"prompt_dump_enable": True, "prompt_dump_max_entries": 2})
    for text in ["1", "2", "3"]:
        prompt_dump.record(runtime, _messages(text))
    rows = prompt_dump.load_recent(runtime.bot_id)
    assert [r["messages"][1]["content"] for r in rows] == ["2", "3"]


def test_malformed_line_does_not_break_reads():
    """上次写崩留下的半行不该让整个文件读不出来。"""
    runtime = _Runtime({"prompt_dump_enable": True})
    prompt_dump.record(runtime, _messages("好行"))
    with open(prompt_dump.dump_path(runtime.bot_id), "a", encoding="utf-8") as f:
        f.write("{半行\n")
    rows = prompt_dump.load_recent(runtime.bot_id)
    assert [r["messages"][1]["content"] for r in rows] == ["好行"]


def test_clear_removes_file():
    runtime = _Runtime({"prompt_dump_enable": True})
    prompt_dump.record(runtime, _messages())
    assert prompt_dump.clear(runtime.bot_id) is True
    assert prompt_dump.load_recent(runtime.bot_id) == []
    assert prompt_dump.clear(runtime.bot_id) is False


# ==================== 绝不抛 ====================


def test_record_never_raises_on_unserializable_payload():
    runtime = _Runtime({"prompt_dump_enable": True})
    bad = [{"role": "user", "content": {1, 2, 3}}]  # set 不可 JSON 序列化
    assert prompt_dump.record(runtime, bad) is False
    assert prompt_dump.load_recent(runtime.bot_id) == []


def test_max_entries_falls_back_on_garbage():
    assert prompt_dump.max_entries(_Config({"prompt_dump_max_entries": "abc"})) == prompt_dump.DEFAULT_MAX_ENTRIES
    # 0/空值 = 用默认（不是"不保留"：留 0 条会让这个功能变得没意义）
    assert prompt_dump.max_entries(_Config({"prompt_dump_max_entries": 0})) == prompt_dump.DEFAULT_MAX_ENTRIES
    assert prompt_dump.max_entries(_Config({"prompt_dump_max_entries": -5})) == 1, "负数夹到 1"
    assert prompt_dump.max_entries(_Config({"prompt_dump_max_entries": 5})) == 5
    assert prompt_dump.max_entries(_Config()) == prompt_dump.DEFAULT_MAX_ENTRIES


# ==================== 与组装收口打通 ====================


async def test_apply_post_assemble_records_when_enabled():
    runtime = _Runtime({"prompt_dump_enable": True})
    req = PromptRequest(session_id="group_9", user_text="hi", raw_user_text="hi", kind="proactive")
    messages = _messages("hi")

    result = await apply_post_assemble(runtime, None, messages, req=req)
    assert result == messages

    entries = prompt_dump.load_recent(runtime.bot_id, session_id="group_9")
    assert len(entries) == 1
    assert entries[0]["kind"] == "proactive", "req.kind 用于区分普通回复/主动消息/定时任务"
    assert entries[0]["messages"] == messages


async def test_apply_post_assemble_records_post_hook_shape():
    """落盘的是钩子改写后的最终形态——diff 要比对的就是真正发出去的东西。"""
    runtime = _Runtime({"prompt_dump_enable": True})

    async def rewrite(ctx):
        ctx.messages = [{"role": "system", "content": "复刻后的 system"}]

    runtime.llm_hooks.register(stage="post_assemble", handler=rewrite)
    req = PromptRequest(session_id="group_9", user_text="hi", raw_user_text="hi")
    await apply_post_assemble(runtime, None, _messages(), req=req)

    entry = prompt_dump.load_recent(runtime.bot_id)[0]
    assert entry["messages"] == [{"role": "system", "content": "复刻后的 system"}]


async def test_apply_post_assemble_does_not_record_when_aborted():
    runtime = _Runtime({"prompt_dump_enable": True})

    async def drop(ctx):
        ctx.messages = []

    runtime.llm_hooks.register(stage="post_assemble", handler=drop)
    req = PromptRequest(session_id="group_9", user_text="hi", raw_user_text="hi")
    assert await apply_post_assemble(runtime, None, _messages(), req=req) is None
    assert prompt_dump.load_recent(runtime.bot_id) == [], "本轮没有请求，不该留记录"


def test_dump_file_is_jsonl(tmp_path):
    runtime = _Runtime({"prompt_dump_enable": True})
    prompt_dump.record(runtime, _messages("x"), session_id="group_1")
    prompt_dump.record(runtime, _messages("y"), session_id="group_1")

    with open(prompt_dump.dump_path(runtime.bot_id), encoding="utf-8") as f:
        lines = [line for line in f.read().splitlines() if line.strip()]
    assert len(lines) == 2, "一行一条，便于直接 diff/管道处理"
    assert [json.loads(line)["messages"][1]["content"] for line in lines] == ["x", "y"]


# ==================== WebUI 只读端点 ====================


def test_prompt_dump_endpoint_reads_and_clears(container):
    from starlette.testclient import TestClient

    from app.webui.app import create_app

    runtime = _Runtime({"prompt_dump_enable": True})
    prompt_dump.record(runtime, _messages("端点"), session_id="group_1")

    client = TestClient(create_app(container))
    resp = client.get(f"/api/agent/prompt-dumps?bot_id={runtime.bot_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True and body["count"] == 1
    assert body["entries"][0]["messages"] == _messages("端点")

    # 会话过滤
    assert client.get(f"/api/agent/prompt-dumps?bot_id={runtime.bot_id}&session_id=group_x").json()["count"] == 0
    assert client.get(f"/api/agent/prompt-dumps?bot_id={runtime.bot_id}&session_id=group_1").json()["count"] == 1

    resp = client.delete(f"/api/agent/prompt-dumps?bot_id={runtime.bot_id}")
    assert resp.status_code == 200 and resp.json()["removed"] is True
    assert prompt_dump.load_recent(runtime.bot_id) == []
