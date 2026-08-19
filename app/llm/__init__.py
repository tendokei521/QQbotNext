"""框架级 LLM Agent 层（对齐 AstrBot 的 agent/tool/scheduler 架构）。

原 llm_chat_v2 的能力上提至此：Agent 循环 + Tool 注册表 + 会话/历史 +
定时任务(CronManager) + 主动消息 + 状态标签协议。
"""

from __future__ import annotations

import json
import os
import re
import shutil

from app.core.logger import get_logger

logger = get_logger("LLM")

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# 旧模块数据目录（迁移源；llm_chat_v2 已删，幂等跳过）
_LEGACY_DATA_DIR = os.path.join(_PROJECT_ROOT, "module", "modules", "llm_chat_v2")

# bot_id 目录名白名单（数字/字母/下划线/连字符），防路径穿越
_BOT_ID_RE = re.compile(r"^[0-9a-zA-Z_\-]+$")


def llm_data_dir() -> str:
    """LLM 数据目录（历史 / 定时任务 / 主动消息状态）。

    默认 data/llm；可用环境变量 QQBOT_LLM_DATA_DIR 覆盖（测试隔离 / 自定义部署）。
    """
    override = os.environ.get("QQBOT_LLM_DATA_DIR", "").strip()
    if override:
        return override
    return os.path.join(_PROJECT_ROOT, "data", "llm")


def safe_bot_id(bot_id) -> str:
    """把 bot_id 规整为安全的目录名，防路径穿越/非法目录名。

    仅允许 [0-9a-zA-Z_-]；其余字符替换为下划线，空值回退为 unknown。
    """
    s = str(bot_id or "").strip()
    if not _BOT_ID_RE.match(s):
        s = re.sub(r"[^0-9a-zA-Z_\-]", "_", s) or "unknown"
    return s


def bot_data_dir(bot_id) -> str:
    """每个账号一个目录：data/llm/<bot_id>/（不存在则创建）。

    历史 / 定时任务 / 主动消息 全部收纳进对应账号目录，实现磁盘级账号隔离。
    """
    d = os.path.join(llm_data_dir(), safe_bot_id(bot_id))
    os.makedirs(d, exist_ok=True)
    return d


def migrate_legacy_data() -> None:
    """把旧模块目录的历史/定时/主动数据搬到 data/llm（幂等，目标已存在则跳过）。"""
    dst = llm_data_dir()
    os.makedirs(dst, exist_ok=True)
    moved = []

    # history/ 目录
    src_history = os.path.join(_LEGACY_DATA_DIR, "history")
    dst_history = os.path.join(dst, "history")
    if os.path.isdir(src_history) and not os.path.isdir(dst_history):
        shutil.move(src_history, dst_history)
        moved.append("history")

    # tasks_data_*.json
    if os.path.isdir(_LEGACY_DATA_DIR):
        for name in os.listdir(_LEGACY_DATA_DIR):
            if name.startswith("tasks_data_") and name.endswith(".json"):
                src = os.path.join(_LEGACY_DATA_DIR, name)
                target = os.path.join(dst, name)
                if not os.path.exists(target):
                    shutil.move(src, target)
                    moved.append(name)

    # proactive_data.json
    src_proactive = os.path.join(_LEGACY_DATA_DIR, "proactive_data.json")
    dst_proactive = os.path.join(dst, "proactive_data.json")
    if os.path.exists(src_proactive) and not os.path.exists(dst_proactive):
        shutil.move(src_proactive, dst_proactive)
        moved.append("proactive_data.json")

    if moved:
        logger.info(f"已迁移数据文件到 data/llm: {', '.join(moved)}")

    _migrate_bot_dirs()


def _migrate_bot_dirs() -> None:
    """把位于根目录（共享）的 legacy 数据按 bot_id 归位到各自目录，幂等。

    - history/history_*.json：按文件内嵌的 bot_id 字段 → data/llm/<bot>/history/；
    - tasks_data_<bot>.json：→ data/llm/<bot>/tasks_data.json；
    - proactive_data.json：无 bot_id 归属，保留在根目录作兼容回退（各 bot 加载时优先用自家文件）。
    """
    base = llm_data_dir()
    moved = []

    # 历史文件（用内嵌 bot_id 归属）
    src_history = os.path.join(base, "history")
    if os.path.isdir(src_history):
        for name in os.listdir(src_history):
            if not (name.startswith("history_") and name.endswith(".json")):
                continue
            src = os.path.join(src_history, name)
            bot_id = "unknown"
            try:
                with open(src, "r", encoding="utf-8") as f:
                    bot_id = safe_bot_id((json.load(f) or {}).get("bot_id") or "unknown")
            except Exception:
                pass
            dst_dir = os.path.join(bot_data_dir(bot_id), "history")
            os.makedirs(dst_dir, exist_ok=True)
            dst = os.path.join(dst_dir, name)
            if not os.path.exists(dst):
                shutil.move(src, dst)
                moved.append(f"{bot_id}/{name}")

    # 定时任务文件（按文件名后缀早前的 bot_id）
    for name in os.listdir(base):
        if not (name.startswith("tasks_data_") and name.endswith(".json")):
            continue
        bot_id = safe_bot_id(name[len("tasks_data_"):-len(".json")])
        dst = os.path.join(bot_data_dir(bot_id), "tasks_data.json")
        if os.path.exists(dst):
            # 已归位则清理旧扁平文件
            os.remove(os.path.join(base, name))
            continue
        shutil.move(os.path.join(base, name), dst)
        moved.append(f"{bot_id}/tasks_data.json")

    if moved:
        logger.info(f"已按 bot_id 归位 LLM 数据: {', '.join(moved)}")



from .chat import handle  # noqa: E402
from .context import LlmContext, LlmJob  # noqa: E402
from .hooks import ToolCallContext  # noqa: E402
from .skills import skill  # noqa: E402
from .tool import ToolContext, tool  # noqa: E402

__all__ = [
    "handle",
    "LlmContext",
    "LlmJob",
    "ToolContext",
    "ToolCallContext",
    "tool",
    "skill",
    "logger",
    "llm_data_dir",
    "safe_bot_id",
    "bot_data_dir",
    "migrate_legacy_data",
]
