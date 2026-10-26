"""从「Provider 预设」页加完模型回到 Agent 页，模型池下拉必须立刻能选到新模型。

`AgentLayout` 每次进入 ``/agent`` 都会 ``agent.load()``，但 store 在
「同账号 + 已有草稿」时整份跳过 ``GET /api/agent/config`` —— 缓存把
``providerPresets`` / ``providerModels`` 一起冻住了，于是新加的模型要**整页刷新**
（Pinia store 重建、放弃草稿）才出现。「配置」不能重拉（会覆盖草稿），
「Provider 目录」是跨页共享的只读数据，必须单独重拉。

前端没有 JS 测试框架，这里沿用仓库既有做法（见 ``test_dashboard_route_parents`` /
``test_dashboard_skeleton_types``）对源码做契约检查：缓存命中的分支必须刷新目录，
且刷新目录不能顺手把草稿改掉。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STORE = ROOT / "app" / "webui" / "dashboard" / "src" / "stores" / "agentConfig.ts"

#: ``load()`` 里的草稿缓存判断（改写这段逻辑时本测试要一起更新）
CACHE_GUARD = "if (!force && this.loadedFor === botId && Object.keys(this.draft).length > 0)"


def _store_source() -> str:
    assert STORE.is_file(), f"Agent 配置 store 不存在: {STORE}"
    return STORE.read_text(encoding="utf-8")


def _brace_block(source: str, start: int) -> str:
    """从 ``source[start]``（必须是 ``{``）开始按大括号配对截取整段。"""
    assert source[start] == "{", f"起点不是左大括号: {source[start : start + 20]!r}"
    depth = 0
    for offset in range(start, len(source)):
        char = source[offset]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start : offset + 1]
    raise AssertionError("大括号不配对，无法截取函数体")


def _method_body(source: str, signature: str) -> str:
    """按签名找到方法，再取它的函数体（签名本身不含大括号）。"""
    start = source.find(signature)
    assert start != -1, f"store 里找不到方法签名: {signature}"
    return _brace_block(source, source.index("{", start + len(signature)))


def test_cached_load_refreshes_provider_catalog():
    body = _method_body(_store_source(), "async load(force = false)")
    guard_at = body.find(CACHE_GUARD)
    assert guard_at != -1, "load() 里找不到「同账号 + 已有草稿」的缓存命中判断"

    branch = _brace_block(body, body.index("{", guard_at))
    assert "refreshProviderCatalog()" in branch, (
        "load() 命中草稿缓存后直接 return —— Provider 目录不会刷新。"
        "表现：在「Provider 预设」页添加模型后回到 Agent 页，模型池下拉里看不到新模型，"
        "要整页刷新才出现。请在 return 前 await this.refreshProviderCatalog()。"
    )


def test_catalog_refresh_keeps_draft_untouched():
    """修复必须是「只刷新目录」：重拉整份配置会覆盖用户未保存的草稿。"""
    body = _method_body(_store_source(), "async load(force = false)")
    guard_at = body.find(CACHE_GUARD)
    branch = _brace_block(body, body.index("{", guard_at))
    for forbidden in ("clearDraft", "Object.assign(this.draft", "this.draft ="):
        assert forbidden not in branch, (
            f"草稿缓存命中分支里出现了 {forbidden!r}：刷新 Provider 目录不能改动配置草稿。"
        )


def test_refresh_provider_catalog_fetches_both_lists():
    body = _method_body(_store_source(), "async refreshProviderCatalog()")
    assert "'/api/provider-presets'" in body, "目录刷新要重拉连接预设（新连接也要立刻可选）"
    assert "'/api/provider-models'" in body, "目录刷新要重拉模型实例（新模型必须立刻出现在模型池）"
    assert re.search(r"providerModels\s*=", body), "刷新结果必须写回 providerModels"
    assert re.search(r"providerPresets\s*=", body), "刷新结果必须写回 providerPresets"
    assert "preset_name" in body, (
        "模型列表接口本身不带 preset_name，刷新时必须用预设表补齐 —— "
        "否则模型池的「预设 / 模型」标签会退化成裸 id。"
    )
