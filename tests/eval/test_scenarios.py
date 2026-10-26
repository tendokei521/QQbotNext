"""X1 黑盒评测集：读 ``scenarios/*.yaml``，逐个跑真实 ``run_turn``。

每个场景锁四件事（可只声明关心的那几条）：

1. **请求次数**（``expect.requests``）：两轮工具 → 恰好 3 次，多一次就是多烧一份全量上下文；
2. **工具执行顺序**（``expect.tools``）：必须等于模型给出的顺序；
3. **回复关键短语**（``expect.contains`` / ``not_contains``）；
4. **结构化结局**（``expect.reason``）：completed / silent / max_rounds / max_tokens / error ……

另外支持几条量化断言：``first_request_messages``（历史条数）、``first_request_contains``
（老信息还在不在）、``tool_result_contains``（工具回填内容）、``prompt_kind``（请求性质）、
``spill_files_min``（超长结果是否落盘）。

驱动细节见 `tests/eval/harness.py`；怎么加场景见 `tests/eval/README.md`。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from .harness import EvalHarnessError, SeenRequest, load_plan, make_runtime, run_scripted_turn

pytestmark = pytest.mark.eval

SCENARIO_DIR = Path(__file__).parent / "scenarios"

#: 首批场景数下限（对齐 docs/llm-context-log-plan.md §X1 的"至少 12 个"）。
MIN_SCENARIOS = 12


def load_scenarios() -> list[dict]:
    """把 ``scenarios/*.yaml`` 全部读出来（按文件名排序，结果稳定）。"""
    scenarios: list[dict] = []
    for path in sorted(SCENARIO_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        data["_file"] = path.name
        scenarios.append(data)
    return scenarios


SCENARIOS = load_scenarios()


def _ids() -> list[str]:
    return [str(scenario.get("name") or scenario["_file"]) for scenario in SCENARIOS]


def _flatten(messages: list[dict]) -> str:
    return json.dumps(messages, ensure_ascii=False)


def _tool_text(requests: list[SeenRequest]) -> str:
    """所有请求里 ``role: tool`` 回填内容的拼接（用于断言工具结果）。"""
    return "\n".join(
        str(message.get("content") or "")
        for request in requests
        for message in request.messages
        if message.get("role") == "tool"
    )


def _spill_files() -> list[Path]:
    """本用例隔离目录下的 spill 落盘文件（根 conftest 已把数据目录指到 tmp_path）。"""
    root = Path(os.environ.get("QQBOT_LLM_DATA_DIR", ""))
    if not root.is_dir():
        return []
    return sorted(root.rglob("*.txt"))


def _assert_turn(scenario: dict, turn: dict, result: Any, requests: list[SeenRequest], runtime: Any) -> None:
    expect: dict = turn.get("expect") or {}
    label = f"{scenario.get('name', scenario['_file'])} / turn={turn.get('user', '')!r}"

    if "requests" in expect:
        assert len(requests) == int(expect["requests"]), (
            f"{label}：请求次数 {len(requests)} != 期望 {expect['requests']}"
        )
    if "max_requests" in expect:
        assert len(requests) <= int(expect["max_requests"]), (
            f"{label}：请求次数 {len(requests)} 超过上限 {expect['max_requests']}"
        )

    if "tools" in expect:
        actual = [item.get("name") for item in result.tool_results]
        assert actual == list(expect["tools"]), f"{label}：工具顺序 {actual} != 期望 {expect['tools']}"

    for phrase in expect.get("contains") or []:
        assert phrase in result.text, f"{label}：回复缺少关键短语 {phrase!r}，实际 {result.text!r}"
    for phrase in expect.get("not_contains") or []:
        assert phrase not in result.text, f"{label}：回复不该包含 {phrase!r}，实际 {result.text!r}"

    if "reason" in expect:
        assert str(result.reason) == str(expect["reason"]), (
            f"{label}：结局 {result.reason} != 期望 {expect['reason']}"
        )

    if "first_request_messages" in expect:
        assert requests, f"{label}：没有发生请求，无法断言历史条数"
        assert len(requests[0].messages) == int(expect["first_request_messages"]), (
            f"{label}：首请求 messages={len(requests[0].messages)} != 期望 "
            f"{expect['first_request_messages']}"
        )
    for phrase in expect.get("first_request_contains") or []:
        assert requests, f"{label}：没有发生请求，无法断言首请求内容"
        flat = _flatten(requests[0].messages)
        assert phrase in flat, f"{label}：首请求里找不到 {phrase!r}"

    for phrase in expect.get("tool_result_contains") or []:
        text = _tool_text(requests)
        assert phrase in text, f"{label}：工具回填内容里找不到 {phrase!r}，实际 {text[:400]!r}"

    if "prompt_kind" in expect:
        prompt_requests = getattr(runtime, "_eval_prompt_requests", [])
        assert prompt_requests, f"{label}：没有记录到 PromptRequest"
        assert prompt_requests[-1].kind == expect["prompt_kind"], (
            f"{label}：kind={prompt_requests[-1].kind!r} != 期望 {expect['prompt_kind']!r}"
        )

    if "spill_files_min" in expect:
        files = _spill_files()
        assert len(files) >= int(expect["spill_files_min"]), (
            f"{label}：spill 落盘文件 {len(files)} 个，少于期望 {expect['spill_files_min']}"
        )


def _plans_for_turn(scenario: dict, turn: dict, cursor: int) -> tuple[list, int]:
    """取本轮要消费的脚本：优先本轮自带 ``provider_script``，否则从顶层队列按 requests 切。"""
    own = turn.get("provider_script")
    if own is not None:
        return [load_plan(item) for item in own], cursor
    flat = scenario.get("provider_script") or []
    count = (turn.get("expect") or {}).get("requests")
    if count is None:
        raise EvalHarnessError(
            f"{scenario.get('name')}：使用顶层 provider_script 时必须给每轮 expect.requests"
        )
    count = int(count)
    chunk = flat[cursor:cursor + count]
    if len(chunk) < count:
        raise EvalHarnessError(
            f"{scenario.get('name')}：顶层 provider_script 只剩 {len(chunk)} 条，本轮需要 {count} 条"
        )
    return [load_plan(item) for item in chunk], cursor + count


def test_catalog_is_well_formed():
    """场景目录自检：够数、名字唯一、必备字段齐全（避免"漏写一条"静默通过）。"""
    assert len(SCENARIOS) >= MIN_SCENARIOS, f"评测场景只有 {len(SCENARIOS)} 个，至少需要 {MIN_SCENARIOS} 个"

    names = [scenario.get("name") for scenario in SCENARIOS]
    missing = [scenario["_file"] for scenario in SCENARIOS if not scenario.get("name")]
    assert not missing, f"这些场景文件缺少 name：{missing}"
    assert len(set(names)) == len(names), f"场景 name 必须唯一：{names}"

    for scenario in SCENARIOS:
        turns = scenario.get("turns")
        assert turns, f"{scenario['_file']}：turns 不能为空"
        for turn in turns:
            assert "user" in turn, f"{scenario['_file']}：每个 turn 都要有 user"
            expect = turn.get("expect")
            assert isinstance(expect, dict), f"{scenario['_file']}：每个 turn 都要有 expect"
            assert "reason" in expect, f"{scenario['_file']}：expect 必须声明 reason"
            assert "requests" in expect or "max_requests" in expect, (
                f"{scenario['_file']}：expect 必须声明 requests 或 max_requests"
            )
            # 取消场景本来就不该请求模型：允许"没有脚本"，但必须显式声明 abort
            if not (bool(turn.get("provider_script")) or bool(scenario.get("provider_script"))):
                assert turn.get("abort"), (
                    f"{scenario['_file']}：缺少 provider_script（除非该轮声明 abort）"
                )


@pytest.mark.parametrize("scenario", SCENARIOS, ids=_ids())
async def test_scenario(scenario: dict, monkeypatch: pytest.MonkeyPatch):
    """跑一个场景：逐轮驱动真实 run_turn，并按 ``expect`` 断言。"""
    config = dict(scenario.get("config") or {})
    stream = bool(scenario.get("stream", True))
    kind = str(scenario.get("kind") or "chat")
    history = scenario.get("history") or []
    cursor = 0

    for turn in scenario["turns"]:
        plans, cursor = _plans_for_turn(scenario, turn, cursor)
        runtime = make_runtime(config)
        abort_scope = None
        if turn.get("abort"):
            # 场景里写 ``abort: <原因>``：在本轮开始**之前**就取消，
            # 锁住"取消入口 → 不请求模型 → 结局 ABORTED"这条链路。
            from app.agent.loop.turn_runner import AbortScope

            abort_scope = AbortScope()
            abort_scope.cancel(str(turn["abort"]))
        result, requests = await run_scripted_turn(
            runtime,
            user_text=str(turn.get("user") or ""),
            stream_script=plans,
            chat_script=plans,
            stream=stream,
            kind=kind,
            history=history,
            abort=abort_scope,
            monkeypatch=monkeypatch,
        )
        _assert_turn(scenario, turn, result, requests, runtime)

    flat = scenario.get("provider_script")
    if flat is not None:
        assert cursor == len(flat), (
            f"{scenario.get('name')}：顶层 provider_script 还剩 {len(flat) - cursor} 条未消费"
        )
