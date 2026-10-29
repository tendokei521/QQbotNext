"""spill（P5 第 2 步）：大结果**落盘可取回**，而不是被砍掉。

钉住四件事：

1. 超过阈值才落盘，且正文变成"预览 + 路径 + 取回方式"；
2. 同内容幂等（内容寻址：同 sha 同文件，不重复写）；
3. 落盘失败**保留原文**（增强不是必需品，绝不让一次成功调用变失败）；
4. 取回类工具自身与发言通道的结果不落盘（否则套娃/替换掉已发出的气泡）。
"""

from __future__ import annotations

import types
from pathlib import Path

from app.agent.tools.spill import (
    apply_spill,
    preview,
    sha_of_text,
    spill_bounds,
    spill_dir,
)


def _runtime(tmp_path, monkeypatch, **config):
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    base = {"tool_spill_enable": True, "tool_spill_max_bytes": 100}
    base.update(config)
    return types.SimpleNamespace(bot_id=778, config=base)


def test_sha_is_content_addressed():
    assert sha_of_text("同样的内容") == sha_of_text("同样的内容")
    assert sha_of_text("a") != sha_of_text("b")


def test_bounds_fall_back_to_defaults():
    assert spill_bounds({}) == (True, 50000)
    assert spill_bounds({"tool_spill_enable": False})[0] is False
    assert spill_bounds({"tool_spill_max_bytes": "abc"})[1] == 50000


def test_small_results_are_untouched(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path, monkeypatch)

    text, path = apply_spill("短结果", runtime=runtime, session_id="group_1")

    assert text == "短结果" and path == ""


def test_oversized_result_is_spilled_and_previewed(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path, monkeypatch)
    big = "头" * 200 + "尾" * 200

    text, path = apply_spill(big, runtime=runtime, session_id="group_1", preview_budget=300)

    assert path and Path(path).is_file()
    assert Path(path).read_text(encoding="utf-8") == big, "落盘的是全文"
    assert "已落盘" in text and path in text
    assert len(text) < len(big), "预览必须显著短于原文"
    assert text.startswith("头") and "尾" in text


def test_same_content_writes_once(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path, monkeypatch)
    big = "x" * 500

    _, first = apply_spill(big, runtime=runtime, session_id="group_1")
    _, second = apply_spill(big, runtime=runtime, session_id="group_1")

    assert first == second
    files = list(spill_dir(778, "group_1").glob("*.txt"))
    assert len(files) == 1


def test_spill_is_per_session(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path, monkeypatch)
    big = "y" * 500

    _, a = apply_spill(big, runtime=runtime, session_id="group_1")
    _, b = apply_spill(big, runtime=runtime, session_id="group_2")

    assert a != b, "会话目录隔离"


def test_disabled_switch_keeps_the_text(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path, monkeypatch, tool_spill_enable=False)

    text, path = apply_spill("z" * 500, runtime=runtime, session_id="group_1")

    assert path == "" and text == "z" * 500


def test_write_failure_degrades_to_the_original(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path, monkeypatch)
    big = "w" * 500

    def _boom(*args, **kwargs):
        raise OSError("磁盘满了")

    monkeypatch.setattr("app.agent.tools.spill.write_spill", _boom)

    text, path = apply_spill(big, runtime=runtime, session_id="group_1")

    assert path == "" and text == big, "落盘失败必须保留原文"


def test_retrieval_tool_and_speech_are_skipped(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path, monkeypatch)
    big = "v" * 500

    _, recall_path = apply_spill(
        big, runtime=runtime, session_id="group_1", tool_name="recall_tool_result"
    )
    _, speech_path = apply_spill(big, runtime=runtime, session_id="group_1", source="speech")

    assert recall_path == "" and speech_path == ""


def test_preview_keeps_the_retrieval_hint():
    text = preview("a" * 5000, budget=200, path="/tmp/x.txt")

    assert "/tmp/x.txt" in text and "recall_tool_result" in text
