"""四模块合并的配置迁移：9 个旧模块的 bindings → 4 个新模块。

## 映射

| 旧模块 | 新模块 |
|---|---|
| `llm_assembly` + `llm_message_meta` | `llm_prompt` |
| `llm_proactive` + `llm_schedule` | `llm_trigger` |
| `llm_knowledge`（目录已不存在） | 无归宿 → 值进 `dropped` 落日志，行删掉 |

## 纪律（与仓库既有迁移一致）

- **幂等**：`kv["four_modules_merged_v1"]` 标记 + "新行已有值优先"合并；重复跑收敛到同一结果。
- **可回滚**：跑前把 SQLite 文件复制到 `backups/`；旧行删除前其内容已并进新行，且原始数据库有备份。
- **不静默丢**：无归宿的键逐条进 `dropped` 并写 warning 日志。
- **只在真有旧行时才写标记**：新装环境没有旧行，不写标记，之后建号还能迁。
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.logger import logger

#: 幂等标记
MIGRATION_FLAG = "four_modules_merged_v1"

#: 新模块 ← 旧模块（同一个新模块下多个旧模块的配置求并集）
GROUPS: dict[str, tuple[str, ...]] = {
    "llm_prompt": ("llm_assembly", "llm_message_meta"),
    "llm_trigger": ("llm_proactive", "llm_schedule"),
}

#: 没有归宿的旧模块（行删除、值落 dropped）
DROP_ONLY: tuple[str, ...] = ("llm_knowledge",)

#: 参与迁移的全部旧模块 id
LEGACY_MODULES: tuple[str, ...] = tuple(
    old for sources in GROUPS.values() for old in sources
) + DROP_ONLY


@dataclass
class MergeReport:
    """迁移结果（便于契约测试与启动日志）。"""

    migrated: bool = False
    accounts: int = 0
    rows: int = 0
    written: dict[str, int] = field(default_factory=dict)
    dropped: dict[str, dict] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    backup: str = ""

    def summary(self) -> str:
        written = "、".join(f"{k}+{v}" for k, v in self.written.items()) or "无"
        return (
            f"[Migrate] 四模块合并：账号/作用域={self.accounts} 行={self.rows} "
            f"新模块写入={written} dropped={len(self.dropped)} 跳过={len(self.skipped)} "
            f"错误={len(self.errors)}"
        )


def _backup_db(config_service: Any) -> str:
    """跑前备份整个 SQLite 文件（失败只告警，不阻断迁移）。"""
    path = getattr(getattr(config_service, "db", None), "path", "")
    if not path or not Path(path).exists():
        return ""
    target = Path(path).parent.parent / "backups" / f"app.pre-4module-merge.{time.strftime('%Y%m%d-%H%M%S')}.db"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        return str(target)
    except Exception as e:  # noqa: BLE001 —— 备份失败不该阻止迁移（日志告警）
        logger.warning(f"[Migrate] 四模块合并：数据库备份失败（继续迁移）: {e}")
        return ""


def _merge_rows(rows: list[dict]) -> tuple[dict, bool, dict]:
    """把同一新模块的多个旧行合并成 (config, enabled, permission)。"""
    config: dict = {}
    enabled = False
    permission: dict = {}
    for row in rows:
        cfg = row.get("config")
        if isinstance(cfg, dict):
            config.update(cfg)
        enabled = enabled or bool(row.get("enabled"))
        perm = row.get("permission")
        if isinstance(perm, dict) and perm and not permission:
            permission = dict(perm)
    return config, enabled, permission


async def migrate_four_modules(config_service: Any) -> MergeReport:
    """执行 9→4 的 bindings 合并迁移。"""
    report = MergeReport()
    if await config_service.get_kv(MIGRATION_FLAG, "") == "1":
        return report

    # 1) 收集：按 (scope_type, scope_id) 聚合所有旧行
    targets: dict[tuple[str, str], dict[str, dict]] = {}
    for old in LEGACY_MODULES:
        for row in config_service.list_bindings(old):
            scope_type = str(row.get("scope_type") or "account")
            scope_id = str(row.get("scope_id") or "")
            targets.setdefault((scope_type, scope_id), {})[old] = row

    if not targets:
        # 新装环境：没有旧行可迁，不写标记（之后建号还能迁）
        logger.info("[Migrate] 四模块合并：没有可迁移的旧模块行，跳过")
        return report

    report.backup = _backup_db(config_service)
    report.accounts = len({scope for (_t, scope) in targets})

    for (scope_type, scope_id), old_rows in targets.items():
        scope_label = f"{scope_type}:{scope_id or '-'}"
        try:
            for new_id, sources in GROUPS.items():
                present = [old_rows[src] for src in sources if src in old_rows]
                if not present:
                    continue
                merged_cfg, enabled, permission = _merge_rows(present)
                existing = config_service.get_binding(new_id, scope_type, scope_id) or {}
                existing_cfg = existing.get("config") if isinstance(existing.get("config"), dict) else {}
                # 新行已有值优先（重跑时不会覆盖用户后来改的）
                final_cfg = {**merged_cfg, **existing_cfg}
                final_enabled = existing.get("enabled") if existing else enabled
                fields: dict[str, Any] = {"enabled": bool(final_enabled), "config": final_cfg}
                if permission:
                    fields["permission"] = permission
                await config_service.save_binding(new_id, scope_type, scope_id, **fields)
                report.written[new_id] = report.written.get(new_id, 0) + len(final_cfg)
                report.rows += 1

            for old in DROP_ONLY:
                row = old_rows.get(old)
                if row and row.get("config"):
                    report.dropped[scope_label] = dict(row["config"])
                    logger.warning(
                        f"[Migrate] 四模块合并：{scope_label} 的 {old} 无归宿，"
                        f"以下配置已归档丢弃: {sorted((row['config'] or {}).keys())}"
                    )

            for old in old_rows:
                await config_service.db.execute(
                    "DELETE FROM bindings WHERE capability_id=? AND scope_type=? AND scope_id=?",
                    (old, scope_type, scope_id),
                )
        except Exception as e:  # noqa: BLE001 —— 单作用域失败不影响其它作用域
            msg = f"{scope_label}: {e}"
            report.errors.append(msg)
            logger.error(f"[Migrate] 四模块合并失败（该作用域已跳过，旧行保留）: {msg}")

    # 2) 重新从库加载 bindings（内存与库对齐）
    reloader = getattr(config_service, "_load_bindings", None)
    if callable(reloader):
        await reloader()

    # 3) 全部成功才写标记
    if not report.errors:
        await config_service.set_kv(MIGRATION_FLAG, "1")
        report.migrated = True
        logger.info(report.summary())
    else:
        logger.error(
            f"[Migrate] 四模块合并有 {len(report.errors)} 个错误，未写迁移标记（下次启动重试）"
        )
    return report


__all__ = ["GROUPS", "LEGACY_MODULES", "MIGRATION_FLAG", "MergeReport", "migrate_four_modules"]
