"""文档闸门：现行文档必须"能点开、不过时"。

背景：仓库曾长期存在"文档跑在代码前面"的问题——模块合并/配置键删除之后，文档还在描述
已经不存在的 `llm_output` / `assembly_profile`，下一个人（或 Agent）会照着错的做。
这里把三件事变成可执行检查：

1. **现行文档清单固定**：`docs/` 顶层只允许白名单里的文件（其余一律进 `docs/archive/`）；
2. **相对链接必须能打开**（只查现行文档；档案里的链接允许失效）；
3. **现行文档不许再提已删除的模块名/配置键**（除非同一行明确写"已删除/已合并"）。

新增文档时：先在白名单里登记，再写内容。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: 允许留在 docs/ 顶层的"现行文档"（按职责分类见 docs/README.md）
LIVE_DOCS = {
    "README.md",
    "ARCHITECTURE.md",
    "MODULE_DEV.md",
    "PLUGIN_STYLE.md",
    "FEATURE_REGISTRY.md",
    "TOOL_STATE.md",
    "AGENT_CONFIG.md",
    "UI_COPY.md",
    "FRONTEND_VERIFY.md",
    "TEST_POLICY.md",
    "DATA_STATS_DESIGN.md",
    "dashboard-frontend-architecture.md",
}

#: 根目录的现行文档（用户向 + 规范）
ROOT_DOCS = {"README.md", "MAINTENANCE.md", "STYLE.md", "AGENTS.md", "CHANGELOG.md"}

#: 已经不存在的东西：模块合并/删除后留下的名字
DEAD_NAMES = (
    "llm_output",
    "llm_assembly",
    "llm_message_meta",
    "llm_proactive",
    "llm_schedule",
    "llm_knowledge",
    "assembly_profile",
    "behavior_rules_enable",
    "system_prompt_style",
)

_LINK_RE = re.compile(r"\]\(([^)]+)\)")

#: 同一行出现这些词时，允许提到已删除的名字（是在解释"它没了"）
_EXCUSE_WORDS = ("已删除", "已合并", "已移除", "不再", "不存在", "archive")


def _live_paths() -> list[Path]:
    paths = [REPO_ROOT / name for name in ROOT_DOCS if (REPO_ROOT / name).exists()]
    docs = REPO_ROOT / "docs"
    if docs.is_dir():
        paths.extend(sorted(docs.glob("*.md")))
    return paths


def test_docs_top_level_only_contains_live_docs():
    """docs/ 顶层只放现行文档；历史稿一律进 docs/archive/。"""
    docs = REPO_ROOT / "docs"
    extra = sorted(p.name for p in docs.glob("*.md") if p.name not in LIVE_DOCS)
    assert not extra, f"这些文档不是现行文档，请移到 docs/archive/ 或在 docs/README.md 登记: {extra}"


def test_every_live_doc_is_registered():
    """白名单里的文件必须真的存在（删文档时别忘同步清单）。"""
    missing = sorted(name for name in LIVE_DOCS if not (REPO_ROOT / "docs" / name).exists())
    assert not missing, f"docs/ 顶层登记了不存在的文档: {missing}"


@pytest.mark.parametrize("path", _live_paths(), ids=lambda p: p.relative_to(REPO_ROOT).as_posix())
def test_relative_links_resolve(path: Path):
    """现行文档里的相对链接必须能打开（档案不在此列）。"""
    text = path.read_text(encoding="utf-8")
    broken: list[str] = []
    for match in _LINK_RE.finditer(text):
        target = match.group(1).split("#")[0].strip()
        if not target or target.startswith(("http://", "https://", "mailto:")):
            continue
        if not (path.parent / target).exists():
            broken.append(target)
    assert not broken, f"{path.relative_to(REPO_ROOT)} 里有打不开的链接: {broken}"


@pytest.mark.parametrize("path", _live_paths(), ids=lambda p: p.relative_to(REPO_ROOT).as_posix())
def test_live_docs_do_not_describe_dead_modules(path: Path):
    """现行文档不许把已删除的模块/配置键当现状写。"""
    offenders: list[str] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if any(word in line for word in _EXCUSE_WORDS):
            continue
        for name in DEAD_NAMES:
            if name in line:
                offenders.append(f"L{lineno}: {name}")
    assert not offenders, (
        f"{path.relative_to(REPO_ROOT)} 仍在描述已删除的模块/配置键: {offenders}；"
        "如果是在说明'它已经没了'，请在同一行写上'已删除/已合并'"
    )


def test_archive_is_documented():
    """档案目录必须有一份说明，告诉读者'这里不代表现状'。"""
    archive = REPO_ROOT / "docs" / "archive"
    assert (archive / "README.md").exists(), "docs/archive/README.md 丢失：档案需要总说明"
