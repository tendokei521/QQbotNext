"""`recall_tool_result`（P5 第 3 步）：把 spill 落盘的全文取回来。

三件必须证明的事：

1. 分页读取正确（offset/limit、末尾提示）；
2. **路径收严**：只能读本账号 spill 目录内的文件，越界一律拒绝；
3. 工具真的被登记进了"系统工具"三处（注入 / WebUI 清单 / 状态枚举），
   否则就是"存在但界面没有"。
"""

from __future__ import annotations

import types
from pathlib import Path

from app.agent.tools.recall import MAX_LIMIT, build_recall_tool, read_spill
from app.agent.tools.spill import write_spill


def _runtime(tmp_path, monkeypatch, **config):
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    base = {"tool_spill_enable": True, "system_tools_enabled": {}}
    base.update(config)
    return types.SimpleNamespace(bot_id=778, config=base, llm_tools=None)


def _spill(tmp_path, monkeypatch, text: str) -> str:
    _runtime(tmp_path, monkeypatch)     # 只为设置数据目录
    return write_spill(778, "group_1", text)


def test_reads_a_slice_with_paging_hint(tmp_path, monkeypatch):
    path = _spill(tmp_path, monkeypatch, "0123456789" * 100)

    first = read_spill(778, path, offset=0, limit=50)

    assert first.startswith("[第 0-50 / 共 1000 字符")
    assert "offset=50" in first
    assert "0123456789" in first


def test_offset_past_the_end_is_reported(tmp_path, monkeypatch):
    path = _spill(tmp_path, monkeypatch, "只有一点点")

    assert "offset=9999 超出范围" in read_spill(778, path, offset=9999)


def test_limit_is_clamped(tmp_path, monkeypatch):
    path = _spill(tmp_path, monkeypatch, "x" * (MAX_LIMIT + 500))

    chunk = read_spill(778, path, offset=0, limit=10**9)

    assert f"第 0-{MAX_LIMIT}" in chunk


def test_paths_outside_the_spill_dir_are_rejected(tmp_path, monkeypatch):
    outside = tmp_path / "secret.txt"
    outside.write_text("不该被读到", encoding="utf-8")
    _runtime(tmp_path, monkeypatch)      # 设置数据目录，确保 spill 根存在

    assert "PATH_DENIED" in read_spill(778, str(outside))


def test_missing_and_empty_paths_are_errors(tmp_path, monkeypatch):
    _runtime(tmp_path, monkeypatch)
    missing = Path(_spill(tmp_path, monkeypatch, "abc")).with_name("nope.txt")

    assert "缺少 path" in read_spill(778, "")
    assert "文件不存在" in read_spill(778, str(missing))


def test_tool_spec_is_valid_and_registered_everywhere(tmp_path, monkeypatch):
    """注入 / WebUI 清单 / 状态枚举三处都要能看到它。"""
    runtime = _runtime(tmp_path, monkeypatch)
    spec = build_recall_tool(runtime, None)

    assert spec.name == "recall_tool_result"
    assert spec.source == "system"
    assert spec.parameters["required"] == ["path"]

    from app.agent.tools.state import resolve_all_tool_states
    from app.agent.tools.system import list_system_tools

    assert "recall_tool_result" in {item["name"] for item in list_system_tools(runtime)}
    assert "recall_tool_result" in {state.name for state in resolve_all_tool_states(runtime)}


async def test_tool_call_reads_the_file(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path, monkeypatch)
    path = _spill(tmp_path, monkeypatch, "落盘的内容在这里")
    spec = build_recall_tool(runtime, None)

    result = await spec.handler(None, {"path": path})

    assert "落盘的内容在这里" in result
