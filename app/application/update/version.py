"""版本号：解析 / 比对 / 读本地版本。

只支持 ``X.Y.Z[-pre]``（本项目实际用法）；不引入 ``packaging``——
``start.py`` 的教训是"自检环节不能依赖第三方包"，更新检查同理。
"""

from __future__ import annotations

import re
from pathlib import Path

_SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+]([0-9A-Za-z.-]+))?$")


def parse_version(value: str) -> tuple[int, int, int, str]:
    """解析成可比较的四元组；预发布版排在正式版**之前**（1.0.0-rc < 1.0.0）。"""
    match = _SEMVER_RE.match(str(value or "").strip())
    if not match:
        raise ValueError(f"不是合法版本号：{value!r}")
    major, minor, patch, pre = match.groups()
    # 有 pre 时用 "0" 前缀让它在同号正式版之前
    return int(major), int(minor), int(patch), ("0" + pre) if pre else "1"


def compare_versions(left: str, right: str) -> int:
    """``left`` 相对 ``right``：-1 更旧 / 0 相同 / 1 更新。"""
    a, b = parse_version(left), parse_version(right)
    if a[:3] != b[:3]:
        return -1 if a[:3] < b[:3] else 1
    if a[3] == b[3]:
        return 0
    return -1 if a[3] < b[3] else 1


def read_local_version(root: Path | str) -> str:
    """从 ``pyproject.toml`` 读 ``version = "x.y.z"``；读不到返回 ``0.0.0``。"""
    path = Path(root) / "pyproject.toml"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return "0.0.0"
    match = re.search(r'(?m)^\s*version\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else "0.0.0"


def select_kit_asset(assets: list[dict]) -> dict | None:
    """从发行资产里挑免安装包（``*-kit.zip``），优先带 sha256 的。"""
    kits = [
        asset for asset in assets or []
        if str(asset.get("name") or "").endswith("-kit.zip")
    ]
    if not kits:
        return None
    with_hash = [asset for asset in kits if str(asset.get("sha256") or "").strip()]
    return (with_hash or kits)[0]
