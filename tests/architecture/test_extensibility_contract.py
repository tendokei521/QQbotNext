"""跨面一致性闸门：新设施不许绕开既有契约。

本文件原先维护一份「57 条可扩展接缝 + full/half 评级 + 棘轮基线」的清单快照。
按 `docs/TEST_POLICY.md §5`（只保留能自动修复或能明确报错的硬规则），
清单/评级/计数型用例已删除——它们随重构必红，却不提供回归价值。

保留的两条是真规则：

1. 代码里出现的每个 LLM 钩子阶段都必须登记（拼错的阶段名会静默失效：
   注册了但永不触发）；
2. `ServiceAccess` 上新增服务字段时必须同步进 capabilities 清单
   （否则模块 `requires=("xxx",)` 自检永远说自己"缺"）。
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = TESTS_ROOT.parent / "app"

#: LLM 流水线合法的钩子阶段（新增阶段必须同时改这里与 docs）
DECLARED_STAGES = frozenset(
    {
        "pre_request",
        "post_assemble",
        "post_response",
        "pre_send",
        "post_send",
        "post_stream",
        # v3：广播型阶段（流水线只报事实，业务自己订阅）
        "message_observed",
        "bot_sent",
        # P2：轮次级观测——开始 / 逐轮 / 收尾
        "turn_started",
        "step_completed",
        "turn_stopping",
        "turn_completed",
        "session_reset",
    }
)

#: `ServiceAccess` 上不算"服务"的字段（不进 capabilities 清单是合理的）
SERVICE_FIELD_ALLOWLIST = {
    "providers": "data_sources 的历史别名（同一对象）",
    "capabilities": "callable()，是自检入口而非服务",
    "extra": "自由扩展槽，无固定语义",
}

_STAGE_PATTERNS = (
    re.compile(r"_run_stage\(\s*\"([a-z_]+)\""),
    re.compile(r"run_stage\([^,]+,\s*\"([a-z_]+)\""),
    re.compile(r"@llm_hook\(\s*\"([a-z_]+)\""),
    re.compile(r"stage\s*=\s*\"([a-z_]+)\""),
)


def test_every_llm_hook_stage_is_declared():
    """代码里出现的每个钩子阶段都必须已在 `DECLARED_STAGES` 登记。

    拼错的阶段名此前会静默失效（注册了但永不触发），这条断言把它变成加载期错误。
    """
    used: set[str] = set()
    for path in APP_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in _STAGE_PATTERNS:
            used.update(pattern.findall(text))
    undeclared = sorted(used - DECLARED_STAGES)
    assert not undeclared, f"未登记的 LLM 钩子阶段: {undeclared}（新增阶段请同步 DECLARED_STAGES 与文档）"


def test_every_service_access_field_is_declared_in_capabilities():
    """`ServiceAccess` 上新增服务字段时，必须同时在 capabilities 清单里登记。

    否则模块 `requires = ("xxx",)` 的自检永远说自己"缺"——因为它查的是 capabilities。
    """
    from app.application.capabilities import SERVICE_FIELDS
    from app.plugin.base import ServiceAccess

    fields = {f.name for f in dataclasses.fields(ServiceAccess)}
    declared = set(SERVICE_FIELDS) | set(SERVICE_FIELD_ALLOWLIST)
    missing = sorted(fields - declared)
    assert not missing, (
        f"ServiceAccess 字段未进 capabilities: {missing} —— "
        f"请加进 app/application/capabilities.py 的 SERVICE_FIELDS（或写进本文件的 allowlist 并说明理由）"
    )
