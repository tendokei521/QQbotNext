"""在线不变式（P4 第 5 步）：会话日志派生 ↔ 内存历史。

三件事：默认关（有开销）、一致时静默、不一致时**落盘 diff 并留痕**——而不是让
"模型看到的上下文和日志里记的不一样"这种沉默故障继续漂移。
"""

from __future__ import annotations

import json
import types

from app.agent.session.manager import SessionManager
from app.agent.session.verify import verify_history_projection


def _runtime(tmp_path, *, verify: bool):
    runtime = types.SimpleNamespace(
        bot_id=778,
        config={"prompt_dump_verify": verify},
    )
    return runtime


def _mgr(tmp_path) -> SessionManager:
    mgr = SessionManager("778")
    mgr.history.history_dir = str(tmp_path / "history")
    return mgr


def _session_with(mgr, texts):
    mgr.create_session("group_1", "group", 60)
    for text in texts:
        mgr.add_message("group_1", "user", text)
    return mgr.get_session("group_1")


def test_default_off_costs_nothing(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    session = _session_with(mgr, ["你好"])

    seen: list[str] = []

    def _boom(*args, **kwargs):
        seen.append("called")
        raise AssertionError("关掉开关时不该做投影比对")

    monkeypatch.setattr("app.agent.session.verify.SessionLog", _boom, raising=False)

    assert verify_history_projection(_runtime(tmp_path, verify=False), mgr, session) is None
    assert seen == []


def test_consistent_history_is_silent(tmp_path):
    mgr = _mgr(tmp_path)
    session = _session_with(mgr, ["你好", "再问一句"])

    assert verify_history_projection(_runtime(tmp_path, verify=True), mgr, session) is None


def test_drift_is_reported_and_dumped(tmp_path):
    mgr = _mgr(tmp_path)
    session = _session_with(mgr, ["你好", "再问一句"])
    # 模拟漂移：内存里被别的路径改了一条，日志没记
    session.data.history[0]["content"] = "被谁改过了"

    report = verify_history_projection(_runtime(tmp_path, verify=True), mgr, session)

    assert report is not None
    assert report["first_diff_index"] == 0
    assert report["derived"][0][1] == "你好" and report["actual"][0][1] == "被谁改过了"
    dump = json.loads(open(report["path"], encoding="utf-8").read())
    assert dump["first_diff_index"] == 0
    assert dump["session_id"] == "group_1"


def test_legacy_history_without_events_is_not_a_drift(tmp_path):
    """存量库还没迁移：无从比较，不能报警。"""
    mgr = _mgr(tmp_path)
    mgr.create_session("group_1", "group", 60)
    session = mgr.get_session("group_1")
    session.data.history.append({"role": "user", "content": "老数据"})

    assert verify_history_projection(_runtime(tmp_path, verify=True), mgr, session) is None

def test_request_fingerprint_is_deterministic_and_logged(tmp_path):
    """Q3：同输入必须同指纹；落库的 request/header 必须与它一致。"""
    from app.agent.session.session_log import EVENT_REQUEST_HEADER, SessionLog, request_header, safe_append
    from app.agent.session.verify import verify_request_reproducible

    mgr = _mgr(tmp_path)
    session = _session_with(mgr, ["你好"])
    runtime = _runtime(tmp_path, verify=True)
    SessionLog(mgr.history, session.task_id)  # 确保 schema 就绪
    messages = [
        {"role": "system", "content": "你是助手"},
        {"role": "user", "content": "你好"},
    ]
    tools = [{"type": "function", "function": {"name": "probe", "description": "d", "parameters": {}}}]
    params = {"temperature": 0.7, "max_tokens": 128}

    # 一致：先落库，再自检
    log = SessionLog(mgr.history, session.task_id)
    safe_append(log, EVENT_REQUEST_HEADER, **request_header(messages, tools, model="m", chain=[], params=params))
    assert verify_request_reproducible(
        runtime, mgr, session, messages, tools, model="m", chain=[], params=params
    ) is None

    # 不一致：日志里是别的请求 → 必须报出来并落盘
    safe_append(log, EVENT_REQUEST_HEADER, **request_header(messages[:-1], tools, model="m", chain=[], params=params))
    report = verify_request_reproducible(
        runtime, mgr, session, messages, tools, model="m", chain=[], params=params,
        where="test",
    )

    assert report is not None
    assert "request/header" in report["problem"]
    assert report["path"] and __import__("pathlib").Path(report["path"]).is_file()


def test_fingerprint_check_is_off_by_default(tmp_path):
    from app.agent.session.verify import verify_request_reproducible

    mgr = _mgr(tmp_path)
    session = _session_with(mgr, ["你好"])
    runtime = _runtime(tmp_path, verify=False)

    assert verify_request_reproducible(
        runtime, mgr, session, [{"role": "user", "content": "x"}], None
    ) is None
