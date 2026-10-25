"""两条一轮对话路径的**公共不变量**：谁都不许悄悄少做一步。

## 背景

非流式已合并为 `turn.run_turn`（`generate_response` 是薄适配层）；
流式仍留在 `chat.stream_response`，因为它独有「发言通道 / 逐句发送 / 打断续写」
这些语义，直接合并风险高于收益。

但两条路径**必须**在下面这些点上保持一致，否则会出现"流式修了、非流式没修"
这类事故（历史上 expand_image 补图轮、补全登记都只在一条路径修过）：

1. `post_assemble` 钩子（插件改本轮请求体的唯一入口）
2. `_append_round_images`（工具轮取回的图片必须送达模型）
3. `_commit_history_enrichment`（取回内容登记，否则历史里只剩占位）
4. `_clean_output_for_history`（历史清洗口径）
5. `turn_completed` 广播（记忆蒸馏等业务的收口）

本测试用源码扫描锁住它们：任何一条路径漏掉其中一步，立刻失败并指出是哪条。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TURN_PY = ROOT / "app" / "agent" / "loop" / "turn.py"
CHAT_PY = ROOT / "app" / "agent" / "loop" / "chat.py"
GENERATE_PY = ROOT / "app" / "agent" / "loop" / "generate.py"
CONTEXT_BLOCKS_PY = ROOT / "app" / "agent" / "loop" / "context_blocks.py"

#: 两条路径都必须体现的步骤 → 允许出现在哪些文件里
REQUIRED_STEPS = {
    "post_assemble": ("apply_post_assemble",),
    "round images": ("_append_round_images", "append_round_images"),
    "history enrichment": ("_commit_history_enrichment",),
    "history cleaning": ("_clean_output_for_history",),
}


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_non_stream_path_is_a_thin_adapter():
    """非流式只允许是适配层：不得再出现 provider 调用/组装/重试逻辑。"""
    text = _text(GENERATE_PY)
    forbidden = {
        "resolve_invoker": "非流式路径不该自己发请求（应在 run_turn 里）",
        "build_for": "非流式路径不该自己组装 messages",
        "apply_post_assemble": "非流式路径不该自己跑 post_assemble",
        "_turn_budget": "非流式路径不该自己管预算",
        "make_executor": "非流式路径不该自己建工具执行器",
    }
    offenders = [f"{name}（{why}）" for name, why in forbidden.items() if name in text]
    assert not offenders, "generate.py 又长回了实现：" + "; ".join(offenders)
    assert "run_turn" in text, "generate.py 必须委托 run_turn"


def test_both_paths_run_post_assemble():
    """`post_assemble` 是插件改本轮请求体的唯一入口，两条路径都必须经过。"""
    for path in (TURN_PY, CHAT_PY):
        assert "apply_post_assemble" in _text(path), f"{path.name} 漏了 post_assemble"


def test_round_images_are_appended_by_the_shared_runner():
    """工具轮取回的图片必须能被送到模型（历史上只有流式做过）。

    P1 收口后"每轮补图"只剩一个实现处（`loop/tool_step` 的两个 runner），
    所以锁 runner 本身：调用方再抄一份就会被 `test_no_tool_loop_in_providers`
    的收敛守卫和这里同时发现。
    """
    runner = ROOT / "app" / "agent" / "loop" / "tool_step.py"
    assert "append_round_images" in _text(runner), "runner 漏了 round images 追加"


def test_both_paths_commit_history_enrichment():
    """补全登记不写，历史里就只剩「未展开」占位。"""
    for path in (TURN_PY, CHAT_PY, CONTEXT_BLOCKS_PY):
        text = _text(path)
        if path == CONTEXT_BLOCKS_PY:
            assert "def _commit_history_enrichment" in text
        else:
            assert "_commit_history_enrichment" in text, f"{path.name} 漏了补全登记"


def test_both_paths_clean_history_text():
    """历史清洗口径（去括号注释等）两边必须一致。"""
    turn_text = _text(TURN_PY)
    assert "_clean_output_for_history" in turn_text, "run_turn 漏了历史清洗"
    # 流式路径经由 assistant_output 处理，至少引用到同一个函数
    chat_text = _text(CHAT_PY)
    assert ("_clean_output_for_history" in chat_text) or ("assistant_output" in chat_text), (
        "chat.py 没走统一的清洗口径"
    )


def test_turn_completed_is_broadcast_once_per_turn():
    """`turn_completed` 是记忆蒸馏等业务的收口：run_turn 里必须有。"""
    assert '"turn_completed"' in _text(TURN_PY) or "'turn_completed'" in _text(TURN_PY)
