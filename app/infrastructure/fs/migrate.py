"""一次性目录迁移：仓库根的 `module/` → `data/module/`（幂等）。

## 为什么要搬

`module/` 曾是「插件运行时数据根」（`configs/` `data/` `plugins/` `uninstalled_modules.json`），
与 `data/`（SQLite / LLM 历史）并列成**两套**运行时目录。业务代码搬进 `app/plugins/` 之后，
它只剩数据却仍叫 `module`：找数据要看两个地方，`.gitignore` 也要维护两条，
而且它和 `app/plugin/`（插件框架）、`app/plugins/`（业务插件）三个"module"概念持续撞车。

现在唯一运行时候数据根是 `data/module/`，本函数负责把旧目录**整体搬过去**。

## 契约

- **只移动，不改写内容**：`data/` `configs/` `plugins/` 原样搬，文件名/结构不变；
- **目标优先**：目标已有同名条目时保留目标（那份可能已被新版本写过），把旧的那份记进
  ``conflicts`` 而不覆盖——宁可留一份多余文件，也不能吃掉用户数据；
- **搬完收尾**：旧目录空了就删；有冲突残留就保留并在日志里点名，交给人工处理；
- **失败不致命**：任何异常都记日志并中止，绝不让启动因为迁移失败而崩；

## 调用点

`app.bootstrap.run()`，在 `build_container()` 之前——容器里的模块注册表/软卸载服务
都按 `Settings.module_dir` 取路径，必须在它们第一次读盘前把目录就位。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from app.core.logger import get_logger

logger = get_logger("Migrate")


def migrate_legacy_module_dir(module_dir: Path | str, project_root: Path | str) -> list[str]:
    """把 `<project_root>/module/` 整棵树搬到 `module_dir`（默认 `data/module`）。

    返回**实际搬过去**的相对路径清单（空表示无事可做或目标已就位）；
    幂等：旧目录不存在时直接返回空清单。
    """
    legacy = Path(project_root) / "module"
    target = Path(module_dir)

    try:
        if not legacy.is_dir():
            return []
        if legacy.resolve() == target.resolve():
            # 用户显式把 MODULE_DIR 指回旧位置：没有可搬的，也不该删它
            return []
        target.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.exception(f"[Migrate] 准备模块目录迁移失败（跳过）: {e}")
        return []

    moved: list[str] = []
    conflicts: list[str] = []
    try:
        _merge_move(legacy, target, moved, conflicts, root=legacy)
    except OSError as e:
        logger.exception(f"[Migrate] 迁移 module/ 中断（已搬 {len(moved)} 项保留在 {target}）: {e}")

    if moved:
        logger.info(f"[Migrate] 已把插件运行时数据从 module/ 搬到 {target}: {', '.join(moved)}")
    if conflicts:
        logger.warning(
            f"[Migrate] 以下旧文件在 {target} 已有同名版本，保留新版本、旧文件未动"
            f"（确认后可手动删除 {legacy}）: {', '.join(conflicts)}"
        )
    _remove_if_empty(legacy)
    return moved


def _merge_move(src: Path, dst: Path, moved: list[str], conflicts: list[str], root: Path) -> None:
    """把 `src` 的内容并入 `dst`（递归）：目标不存在的条目移动过去，存在的保留目标。

    ``moved`` / ``conflicts`` 里记的是相对 ``root`` 的路径，便于日志点名到具体文件。
    """
    for child in sorted(src.iterdir()):
        rel = child.relative_to(root).as_posix()
        dest = dst / child.name
        if child.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
            _merge_move(child, dest, moved, conflicts, root)
            _remove_if_empty(child)
        elif not dest.exists():
            shutil.move(str(child), str(dest))
            moved.append(rel)
        else:
            conflicts.append(rel)


def _remove_if_empty(directory: Path) -> None:
    """空目录就删掉（非空/删不掉都安静跳过——迁移不是清理工具）。"""
    try:
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
    except OSError as e:
        # 竞态（别的进程刚写入）或权限问题：留着不影响功能，记 debug 即可
        logger.debug(f"[Migrate] 删除空目录失败（忽略）: {directory}: {e}")


__all__ = ["migrate_legacy_module_dir"]
