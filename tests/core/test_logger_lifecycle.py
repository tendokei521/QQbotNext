"""日志系统生命周期：重建不泄漏文件句柄、import 不产生磁盘/句柄副作用。

两条都是"只有长时间跑或反复初始化才暴露"的问题：

- `_build_logger` 旧实现直接 ``handlers.clear()``，旧 handler（每个都开着日志文件）
  既没 close 也没 flush，句柄泄漏；Windows 上文件句柄被占用还会让轮转/清理失败。
- 模块级具名 logger 旧实现是 ``get_logger("Main")``，于是**任何一次 import**
  ``app.core.logger`` 都会触发 ``setup_logging()``，创建 ``./logs`` 目录并打开
  5 个文件——单测和命令行诊断脚本都白背这个副作用。
"""

from __future__ import annotations

import importlib
import logging
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _logger_module():
    """取 logger **模块**本身。

    注意不能用 ``from app.core import logger``：`app/core/__init__.py` 里有
    ``from app.core.logger import logger``，包属性 `logger` 已被同名实例覆盖。
    """
    return importlib.import_module("app.core.logger")


def test_rebuild_closes_previous_handlers(tmp_path):
    """重建日志系统时旧 handler 必须先 close，再摘掉。"""
    lg = _logger_module()

    try:
        first_logger = lg._build_logger(tmp_path / "logs_a")
        first_handlers = list(first_logger.handlers)
        assert first_handlers, "初始化后应至少有文件 handler"

        lg._build_logger(tmp_path / "logs_b")

        # FileHandler.close() 会把 stream 置 None：这是句柄确已释放的可观察证据
        # （控制台 StreamHandler 的 stream 是 stdout，不归本用例管）
        file_handlers = [h for h in first_handlers if isinstance(h, logging.FileHandler)]
        assert file_handlers, "初始化后应至少有文件 handler"
        leaked = [h for h in file_handlers if h.stream is not None]
        assert not leaked, f"重建日志系统时 {len(leaked)} 个旧 handler 未 close（句柄泄漏）"
        assert not set(first_handlers) & set(first_logger.handlers)
    finally:
        # 恢复到默认日志目录，避免影响同进程内后续用例
        lg.setup_logging()


def test_importing_logger_module_has_no_filesystem_side_effect(tmp_path):
    """`import app.core.logger` 不应创建 logs 目录、打开日志文件。"""
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    proc = subprocess.run(
        [sys.executable, "-c", "import app.core.logger"],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stderr
    assert not (tmp_path / "logs").exists(), "import 期就建了日志目录：logger 仍是惰性化之前的写法"


def test_lazy_logger_proxy_forwards_methods(tmp_path):
    """惰性代理必须完整转发 PrefixLogger 的接口（add_info 链式调用）。"""
    lg = _logger_module()

    try:
        lg.setup_logging(tmp_path / "logs")
        # 不抛异常即说明 add_info/info 等接口经代理可达
        lg.logger.add_info("Lifecycle").info("代理可用")
        assert lg.logger.default_prefix == "Main"
    finally:
        lg.setup_logging()
