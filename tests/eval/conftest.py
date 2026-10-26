"""X1 黑盒评测集的 pytest 配置。

两件事：

1. 注册 ``eval`` marker（只在这一个目录里注册，**不改全局** ``pytest.ini`` /
   ``pyproject.toml``，避免把评测的约定扩散到全仓库）；
2. 让评测**默认不进全量**：`pytest` / `pytest tests` 里这些用例显示为 skipped，
   只有显式 ``-m eval`` 才真正执行（评测要驱动多轮脚本，比普通单测慢）。

为什么用 skip 而不是靠 marker 自身：marker 只是"打个标签"，不参与默认筛选；
不写这一层的话 ``pytest -q`` 会照样把评测跑一遍，"不进全量"就成了一句空话。
"""

from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "eval: 黑盒行为评测（默认不进全量）")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """没有显式 ``-m eval`` 时跳过本目录的评测用例（收集到了但不执行）。"""
    markexpr = str(getattr(config.option, "markexpr", "") or "")
    if "eval" in markexpr:
        return
    skip = pytest.mark.skip(reason="eval 评测默认不进全量：用 `-m eval` 单独跑")
    for item in items:
        if "eval" in item.keywords:
            item.add_marker(skip)
