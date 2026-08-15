"""框架级 LLM Agent 层（对齐 AstrBot 的 agent/tool/scheduler 架构）。

原 llm_chat_v2 的能力上提至此：Agent 循环 + Tool 注册表 + 会话/历史 +
定时任务(CronManager) + 主动消息 + 状态标签协议。
"""

from __future__ import annotations

import os
import shutil

from app.core.logger import get_logger

logger = get_logger("LLM")

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# 旧模块数据目录（迁移源；llm_chat_v2 已删，幂等跳过）
_LEGACY_DATA_DIR = os.path.join(_PROJECT_ROOT, "module", "modules", "llm_chat_v2")


def llm_data_dir() -> str:
    """LLM 数据目录（历史 / 定时任务 / 主动消息状态）。

    默认 data/llm；可用环境变量 QQBOT_LLM_DATA_DIR 覆盖（测试隔离 / 自定义部署）。
    """
    override = os.environ.get("QQBOT_LLM_DATA_DIR", "").strip()
    if override:
        return override
    return os.path.join(_PROJECT_ROOT, "data", "llm")


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



from .chat import handle  # noqa: E402
from .context import LlmContext, LlmJob  # noqa: E402

__all__ = [
    "handle",
    "LlmContext",
    "LlmJob",
    "logger",
    "llm_data_dir",
    "migrate_legacy_data",
]
