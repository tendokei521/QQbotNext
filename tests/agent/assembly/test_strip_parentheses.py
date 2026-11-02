"""括号清洗测试：strip / maybe_strip 的纯函数语义。

历史：端到端用例曾验证「展示保留原文、写历史时清洗」，但 ``clean_output_parentheses``
开关与那段历史清洗逻辑已删除（2026-11-01 精简）：现在展示与历史都保留原文，
不再有需要跨链路验证的分叉，因此只保留纯函数契约。
"""

from app.domain.tags import maybe_strip_parentheses, strip_parentheses


# ---- strip_parentheses ----
def test_strip_parentheses_basic():
    assert strip_parentheses("好的（笑）我知道了") == "好的我知道了"
    assert strip_parentheses("好的(备注)我知道了") == "好的我知道了"
    assert strip_parentheses("（笑）好的～") == "好的"
    assert strip_parentheses("好的～（这段很简单）") == "好的"


def test_strip_parentheses_keep_unclosed_and_no_nuke():
    # 未闭合括号不剥
    assert strip_parentheses("还在说（没闭合") == "还在说（没闭合"
    # 嵌套：整段都是括号 → 不清空，保留原文（避免触发空回复兜底）
    out = strip_parentheses("（外层(内层)）")
    assert out != ""
    assert "外层" in out
    # 纯括号（单层）同样不清空
    assert strip_parentheses("（微笑）") == "（微笑）"
    # 嵌套但内含正文 → 正常剥
    assert strip_parentheses("（好的（嗯））好的") == "好的"


def test_maybe_strip_parentheses_toggle():
    assert maybe_strip_parentheses("好（哦）", enabled=True) == "好"
    assert maybe_strip_parentheses("好（哦）", enabled=False) == "好（哦）"
