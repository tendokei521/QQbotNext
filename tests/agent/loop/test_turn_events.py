"""Q3：一轮对话在事件日志里留下**可复放**的骨架。

钉住四件事：

1. `request/header` 与实际发出的请求一致（system 指纹、工具集合、链）；
2. `turn/start` / `turn/end` 成对，`turn/end.reason` 与 `TurnResult.reason` 同源；
3. `tool/call` 与 `tool/result` 成对，`source_event_seqs` 指得回那次调用；
4. 这些事件都**只入日志**（`derive_messages` 里看不到），不影响模型可见历史。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent.loop.turn_runner import TurnReason
from app.agent.providers import PROVIDERS
from app.agent.providers.base import StreamEvent
from app.agent.providers.openai_compat import OpenAICompatProvider
from app.agent.session.session_log import (
    EVENT_REQUEST_HEADER,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    EVENT_TURN_END,
    EVENT_TURN_START,
    SessionLog,
    request_header,
)
from app.agent.tools.spec import ToolSpec, build_tools

STREAM_SCRIPT: list[list[StreamEvent]] = []
HEADER_TOOL = ToolSpec(
    name="probe",
    description="探针工具",
    parameters={"type": "object", "properties": {}},
    handler=None,  # 只用于指纹，不执行
    scopes=("*",),
)


class _ScriptedProvider(OpenAICompatProvider):
    async def _stream_once(self, api_key, payload, timeout):  # noqa: ANN001
        for event in STREAM_SCRIPT.pop(0):
            yield event


def _config() -> dict:
    return {
        "history_rounds": 50,
        "max_message_length": 200,
        "max_tool_rounds": 3,
        "empty_reply_retries": 0,
        "reasoning_overflow_retries": 0,
        "stream_output": True,
        "tool_result_directive_enable": False,
    }


class _Session:
    def mark_replied(self):
        return None


@pytest.fixture()
def harness(tmp_path, monkeypatch):
    import app.agent.loop.turn as turn_module
    from app.agent.session.manager import SessionManager

    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    monkeypatch.setitem(PROVIDERS, "scripted", _ScriptedProvider)
    monkeypatch.setattr(
        turn_module.assembly, "build_for", lambda runtime, req: [{"role": "user", "content": "查一下"}]
    )

    mgr = SessionManager("10001")
    session_holder: dict = {}

    async def _fake_prepare(runtime, event, ctx=None, *, session_mgr=None, turn=None, bot=None):
        session = mgr.create_session("group_1", "group", 60)
        session_holder["session"] = session
        prompt_req = SimpleNamespace(
            session_id="group_1", kind="chat", config=runtime.config,
            user_text="查一下", extra_user_content_parts=[], modalities=None,
        )
        meta = {
            "model": "fake-model", "max_tokens": 128, "temperature": 0.7,
            "provider_chain": [{"provider": "scripted", "model": "fake-model"}],
            "modalities": None, "all_specs": [HEADER_TOOL],
            "tool_ctx": SimpleNamespace(extra={}, runtime=None, session_id="group_1", event=None),
            "session": session, "session_mgr": mgr, "is_private": False,
            "user_id": "", "group_id": None, "session_id": "group_1",
        }
        return prompt_req, session, meta

    monkeypatch.setattr(turn_module, "prepare_prompt", _fake_prepare)

    runtime = SimpleNamespace(
        bot_id="10001", config=_config(), invoker=None, bot=None, llm_tools=None,
        skills=None, memory=None, knowledge=None, mcp_manager=None, telemetry=None,
        provider_config=lambda: {"provider": "scripted", "model": "fake-model", "api_key": "sk-test"},
        provider_chain=lambda: [{"provider": "scripted", "model": "fake-model"}],
        llm_hooks=None, llm_tool_call_hooks=None,
    )
    return SimpleNamespace(module=turn_module, runtime=runtime, mgr=mgr, holder=session_holder)


async def _run(harness):
    return await harness.module.run_turn(
        harness.runtime,
        harness.module.TurnRequest(session_id="group_1", is_private=False, user_text="查一下", stream=True),
    )


def _events(harness) -> list[dict]:
    session = harness.holder["session"]
    return SessionLog(harness.mgr.history, session.task_id).events()


async def test_turn_events_are_logged_and_replayable(harness):
    STREAM_SCRIPT.extend([
        [StreamEvent(type="text", text="查到了。"), StreamEvent(type="done")],
    ])

    result = await _run(harness)
    events = _events(harness)
    types = [e["type"] for e in events]

    assert result.reason is TurnReason.COMPLETED
    assert types[0] == EVENT_TURN_START
    assert EVENT_REQUEST_HEADER in types
    assert types[-1] == EVENT_TURN_END, "turn/end 必须是最后一条（收尾在请求之后）"

    header = next(e for e in events if e["type"] == EVENT_REQUEST_HEADER)["payload"]
    # 可复放：同一份 messages/tools 重新算指纹，必须与日志里一致
    replay = request_header(
        result.messages,
        build_tools([HEADER_TOOL]),
        model="fake-model",
        chain=[{"provider": "scripted", "model": "fake-model"}],
        params={"temperature": 0.7, "max_tokens": 128},
    )
    assert header["system_hash"] == replay["system_hash"]
    assert [t["name"] for t in header["tools"]] == ["probe"]
    assert header["chain"] == ["scripted/fake-model"]

    end = events[-1]["payload"]
    assert end["reason"] == "completed"
    assert end["rounds"] == 1


async def test_tool_call_and_result_are_paired(harness):
    STREAM_SCRIPT.extend([
        [
            StreamEvent(type="tool_call", tool_call={
                "index": 0, "id": "c0",
                "function": {"name": "probe", "arguments": '{"x": 1}'},
            }),
            StreamEvent(type="done", finish_reason="tool_calls"),
        ],
        [StreamEvent(type="text", text="查到了。"), StreamEvent(type="done")],
    ])

    await _run(harness)
    events = _events(harness)
    calls = [e for e in events if e["type"] == EVENT_TOOL_CALL]
    results = [e for e in events if e["type"] == EVENT_TOOL_RESULT]

    assert len(calls) == 1 and len(results) == 1
    assert calls[0]["payload"]["call_id"] == "c0"
    assert calls[0]["payload"]["name"] == "probe"
    assert calls[0]["payload"]["args_raw"] == '{"x": 1}'
    # 回指：result 必须指回那次 call 的 seq
    assert results[0]["payload"]["source_event_seqs"] == [calls[0]["seq"]]
    assert results[0]["payload"]["call_id"] == "c0"
    # 只入日志：投影里看不到工具事件
    session = harness.holder["session"]
    derived = SessionLog(harness.mgr.history, session.task_id).derive_messages()
    assert all(str(m.get("role")) in ("user", "assistant") for m in derived)

async def test_abort_scope_stops_the_turn_before_the_request(harness):
    """Q2/P2：取消入口在 `TurnRequest.abort` 上——已取消的一轮不请求模型。"""
    from app.agent.loop.turn_runner import AbortScope, TurnReason

    scope = AbortScope()
    scope.cancel("被新消息取代")
    STREAM_SCRIPT.clear()

    result = await harness.module.run_turn(
        harness.runtime,
        harness.module.TurnRequest(
            session_id="group_1", is_private=False, user_text="查一下",
            stream=True, abort=scope,
        ),
    )

    assert result.reason is TurnReason.ABORTED
    assert result.interrupted is True
    assert result.text == ""
    # 事件日志里也要能看到这一轮（start + end，reason=aborted）
    events = _events(harness)
    assert events[0]["type"] == EVENT_TURN_START
    assert events[-1]["type"] == EVENT_TURN_END
    assert events[-1]["payload"]["reason"] == "aborted"
