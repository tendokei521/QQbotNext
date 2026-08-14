"""日志系统。

保留原 basic/logger.py 的 6 小时轮转、分级别文件、归档与前缀 Logger 能力，
日志目录改为由 Settings 注入，避免硬编码 ./logs。
"""

from __future__ import annotations

import inspect
import logging
import os
import re
import shutil
import sys
import time
from datetime import datetime, timedelta
from logging.handlers import BaseRotatingHandler
from pathlib import Path


def setup_dir(folder_path: str | Path) -> None:
    Path(folder_path).mkdir(parents=True, exist_ok=True)


class SixHourRotatingHandler(BaseRotatingHandler):
    """每 6 小时轮转日志，按级别拆分 debug/warn/errors，并清理过期归档。"""

    ROTATION_INTERVAL = 6 * 60 * 60  # 21600 秒

    def __init__(self, filename, level=logging.NOTSET, backup_count=48):
        self.base_filename = filename
        self.backup_count = backup_count
        self.logs_dir = os.path.dirname(os.path.abspath(filename)) or "./logs"
        setup_dir(self.logs_dir)
        self._recover_recent_logs()
        self.next_rollover_time = self._compute_next_rollover()
        super().__init__(filename, mode="a", encoding="utf-8")
        self.setLevel(level)

    def _compute_next_rollover(self):
        now = datetime.now()
        current_segment = now.hour // 6
        next_segment = current_segment + 1
        if next_segment >= 4:
            return (
                now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            ).timestamp()
        return now.replace(hour=next_segment * 6, minute=0, second=0, microsecond=0).timestamp()

    def _get_archive_folder(self, timestamp=None):
        dt = datetime.fromtimestamp(timestamp if timestamp is not None else time.time())
        hour_segment = (dt.hour // 6) * 6
        folder_name = dt.strftime(f"%Y-%m-%d-{hour_segment:02d}")
        return os.path.join(self.logs_dir, folder_name)

    def _recover_recent_logs(self):
        now = datetime.now()
        archive_folder = self._get_archive_folder(now.timestamp())
        if not os.path.exists(archive_folder):
            return
        base_name = os.path.basename(self.base_filename)
        archive_file = os.path.join(archive_folder, base_name)
        if not os.path.exists(archive_file):
            return
        try:
            with open(archive_file, "r", encoding="utf-8") as f:
                archived_content = f.read()
            with open(self.base_filename, "a", encoding="utf-8") as f:
                f.write(archived_content)
            os.remove(archive_file)
            if not os.listdir(archive_folder):
                os.rmdir(archive_folder)
        except Exception as e:  # pragma: no cover
            print(f"恢复日志失败: {e}")

    def shouldRollover(self, record):
        return 1 if time.time() >= self.next_rollover_time else 0

    def doRollover(self):
        self.stream.close()
        archive_time = self.next_rollover_time - self.ROTATION_INTERVAL
        archive_folder = self._get_archive_folder(archive_time)
        setup_dir(archive_folder)
        base_name = os.path.basename(self.base_filename)
        archive_file = os.path.join(archive_folder, base_name)
        if os.path.exists(self.base_filename):
            try:
                if os.path.exists(archive_file):
                    with open(self.base_filename, "r", encoding="utf-8") as src:
                        content = src.read()
                    with open(archive_file, "a", encoding="utf-8") as dst:
                        dst.write(content)
                    os.remove(self.base_filename)
                else:
                    shutil.move(self.base_filename, archive_file)
            except Exception as e:  # pragma: no cover
                print(f"归档日志失败: {e}")
        self._cleanup_old_archives()
        self.stream = self._open()
        self.next_rollover_time = self._compute_next_rollover()

    def _cleanup_old_archives(self):
        try:
            folders = []
            for item in os.listdir(self.logs_dir):
                item_path = os.path.join(self.logs_dir, item)
                if os.path.isdir(item_path) and re.match(r"\d{4}-\d{2}-\d{2}-\d{2}", item):
                    folders.append((item, item_path))
            folders.sort(key=lambda x: x[0])
            while len(folders) > self.backup_count:
                _, old_folder_path = folders.pop(0)
                shutil.rmtree(old_folder_path, ignore_errors=True)
        except Exception as e:  # pragma: no cover
            print(f"清理归档时出错: {e}")


# 调用者模块缓存：{ (filename, lineno): (module, lineno) }，同一条日志行多次调用只回溯一次
_caller_cache: dict = {}


def _find_caller_module() -> tuple | None:
    """栈回溯找第一个业务调用者 → (模块名, 行号)。

    跳过 logging 内部与本文件（format 等）帧；找不到返回 None。
    """
    f = inspect.currentframe()
    try:
        # f → _find_caller_module；f.f_back → CallerModuleFormatter.format
        frame = f.f_back.f_back if f.f_back else None  # logging 内部帧开始
        while frame is not None:
            mod = frame.f_globals.get("__name__", "")
            if mod and not mod.startswith("logging") and mod != __name__:
                key = (frame.f_code.co_filename, frame.f_lineno)
                cached = _caller_cache.get(key)
                if cached:
                    return cached
                caller = (mod, frame.f_lineno)
                _caller_cache[key] = caller
                return caller
            frame = frame.f_back
        return None
    finally:
        del f


def _truncate_module(module: str) -> str:
    """截断模块路径各部分：长度 ≤8 取前 6 字符，≥9 取前 7 字符。"""
    return ".".join(
        (part[:6] if len(part) <= 8 else part[:7])
        for part in module.split(".")
    )


class CallerModuleFormatter(logging.Formatter):
    """把记录名动态改为「调用者模块路径:行号」（替代固定的 [Service]），带方括号输出。"""

    def format(self, record: logging.LogRecord) -> str:
        caller = _find_caller_module()
        if caller:
            mod, lineno = caller
            record.name = f"{_truncate_module(mod)}:{lineno}"
        else:
            # 边缘情况：找不到调用者 → 去掉原名的方括号，避免 [[Service]]
            record.name = record.name.strip("[]") or "?"
        return super().format(record)


def _build_logger(log_dir: str | Path) -> logging.Logger:
    logs_dir = str(log_dir)
    setup_dir(logs_dir)
    logger_ = logging.getLogger("[Service]")
    logger_.setLevel(logging.DEBUG)
    logger_.handlers.clear()
    logger_.propagate = False

    lf = CallerModuleFormatter(
        fmt="[%(name)s] %(asctime)s.%(msecs)03d - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    for level, filename in ((logging.DEBUG, "debug.log"), (logging.WARNING, "warn.log"), (logging.ERROR, "errors.log")):
        handler = SixHourRotatingHandler(filename=os.path.join(logs_dir, filename), level=level, backup_count=48)
        handler.setFormatter(lf)
        logger_.addHandler(handler)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG)
    console.setFormatter(lf)
    logger_.addHandler(console)
    return logger_


class PrefixLogger(logging.LoggerAdapter):
    """带前缀的 Logger 适配器，支持链式加标签：logger.add_info('#0').add_info('模块')。"""

    def __init__(self, logger: logging.Logger, default_prefix: str):
        super().__init__(logger, {"prefix": default_prefix})
        self.default_prefix = default_prefix

    def process(self, msg, kwargs):
        prefix = kwargs.pop("prefix", self.default_prefix)
        return f"[{prefix}] {msg}", kwargs

    def prefix(self, prefix: str) -> "PrefixLogger":
        return PrefixLogger(self.logger, prefix)

    def add_info(self, info: str) -> "PrefixLogger":
        return PrefixLogger(self.logger, f"{self.default_prefix}] [{info}")

    def new(self) -> "PrefixLogger":
        return PrefixLogger(self.logger, self.default_prefix)

    def prefix_self(self, prefix: str) -> None:
        self.default_prefix = prefix

    def add_info_self(self, info: str) -> None:
        self.default_prefix = f"{self.default_prefix}] [{info}"


_root_logger: logging.Logger | None = None


def setup_logging(log_dir: str | Path = "logs") -> PrefixLogger:
    """初始化日志系统（应用启动时调用一次），返回主 Logger。"""
    global _root_logger
    _root_logger = _build_logger(log_dir)
    return PrefixLogger(_root_logger, "Main")


def get_logger(prefix: str = "Main") -> PrefixLogger:
    """获取指定前缀的 Logger；未初始化时以默认目录兜底。"""
    if _root_logger is None:
        setup_logging()
    return PrefixLogger(_root_logger, prefix)


def get_module_logger(module_name: str) -> PrefixLogger:
    return get_logger("Module").add_info(module_name)


# 常用具名 Logger（保持与原 basic.logger 一致的调用习惯）
logger = get_logger("Main")
webui_logger = get_logger("WebUI")
websocket_logger = get_logger("WebSocket")
api_logger = get_logger("API")
task_logger = get_logger("Task")
module_logger = get_logger("Module")
