"""校验 ``v-skeleton-loader`` 的 ``type`` 只用 Vuetify 真正认得的类型名。

为什么值得单独立一条测试：``type`` 里出现一个未登记的名字（典型是 Vuetify 2 时代
的 ``card-heading``）不会在构建期报错，而是在**渲染骨架屏的瞬间**抛

    TypeError: Cannot read properties of undefined (reading 'includes')

因为 Vuetify 内部按 ``rootTypes[token]`` 取值后直接 ``.includes(',')``。骨架屏通常
挂在首屏 ``v-if="loading"`` 分支里，于是整个路由树卸载 —— 表现为整个前端白屏，
且控制台只有一句难以定位的压缩栈。

历史上 ``OverviewPage`` 与 ``ModuleConfigPage`` 都用了 ``card-heading``，导致
首页直接白屏。类型表从已安装的 Vuetify 源码里读，避免和依赖版本脱节。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_DIR = ROOT / "app" / "webui" / "dashboard"
VUETIFY_SKELETON_SRC = (
    DASHBOARD_DIR
    / "node_modules"
    / "vuetify"
    / "lib"
    / "components"
    / "VSkeletonLoader"
    / "VSkeletonLoader.mjs"
)
SRC_DIR = DASHBOARD_DIR / "src"

# <v-skeleton-loader ... type="a, b, c" ...>（含跨行属性，但不含 :type 绑定）
_STATIC_TYPE_ATTR = re.compile(r"<v-skeleton-loader\b[^>]*?\btype=\"([^\"]*)\"", re.S)


def _valid_type_names() -> set[str]:
    """从 Vuetify 源码的 ``export const rootTypes = {...}`` 里取合法类型名。"""
    text = VUETIFY_SKELETON_SRC.read_text(encoding="utf-8")
    _, _, tail = text.partition("export const rootTypes = {")
    block = tail.split("\n};", 1)[0]
    names: set[str] = set()
    for line in block.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key = line.split(":", 1)[0].strip().strip("'\"")
        if key:
            names.add(key)
    return names


def _static_skeleton_types() -> list[tuple[str, str, str]]:
    """收集所有静态 type 用法：[(相对文件, 原始值, token)]。"""
    found: list[tuple[str, str, str]] = []
    for path in sorted(SRC_DIR.rglob("*.vue")):
        content = path.read_text(encoding="utf-8")
        for match in _STATIC_TYPE_ATTR.finditer(content):
            raw = match.group(1)
            for token in raw.split(","):
                token = token.strip()
                if not token:
                    continue
                # Vuetify 允许 name@count 形式；校验时只看类型名部分
                found.append((str(path.relative_to(ROOT)), raw, token.split("@")[0]))
    return found


def test_vuetify_skeleton_source_available():
    assert VUETIFY_SKELETON_SRC.is_file(), (
        f"未找到 Vuetify 源码，无法校验 skeleton 类型名: {VUETIFY_SKELETON_SRC}\n"
        "若依赖未安装，请先在 app/webui/dashboard/ 下执行 pnpm install"
    )


def test_skeleton_loader_types_are_known_to_vuetify():
    if not VUETIFY_SKELETON_SRC.is_file():
        pytest.skip("vuetify 未安装，跳过 skeleton 类型校验")

    valid = _valid_type_names()
    assert valid, "未能从 Vuetify 源码解析出任何 skeleton 类型名，解析逻辑可能已失效"
    # 记录几个关键锚点，解析退化时能立刻发现
    assert {"actions", "article", "heading", "list-item-two-line"} <= valid

    invalid = [
        (path, raw, token)
        for path, raw, token in _static_skeleton_types()
        if token not in valid
    ]
    assert not invalid, "以下 v-skeleton-loader 使用了 Vuetify 不认得的类型名（会导致白屏）: " + "; ".join(
        f"{path} -> {raw!r}（非法 token: {token}）" for path, raw, token in invalid
    )


def test_every_skeleton_loader_has_static_type():
    """动态绑定 :type 无法在编译期校验，容易再把白屏问题带回来。"""
    dynamic: list[str] = []
    for path in sorted(SRC_DIR.rglob("*.vue")):
        content = path.read_text(encoding="utf-8")
        for match in re.finditer(r"<v-skeleton-loader\b[^>]*>", content, re.S):
            tag = match.group(0)
            if ":type=" in tag:
                dynamic.append(str(path.relative_to(ROOT)))
    assert not dynamic, f"以下文件的 v-skeleton-loader 使用了动态 :type，改为静态字符串才能被校验: {dynamic}"
