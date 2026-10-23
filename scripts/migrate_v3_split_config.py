"""v3 配置拆分迁移：把 agent 配置行里的 108 个键搬到 9 个模块行。

## 为什么必须迁

v3 把 108 个键从 agent 配置页搬进了 9 个功能模块（`config_partition.MODULE_KEYS`），
且 `config_schema.SCHEMA` 已收敛到 43 键。**用户存在 agent 行里的旧值必须跟着搬家**，
否则升级后等于把配置清空（例如原本开着的记忆、主动消息会全部回到默认关闭）。

## 规则

| 键 | 处理 |
|---|---|
| 43 个本体键 | 留在 agent 行 |
| 108 个模块键 | 搬到对应模块行；**模块行已有同名键时以模块为准**（不覆盖用户新配的） |
| 9 个废弃键 | 从 agent 行删除，值记进 `logs/migrate_v3_dropped_keys.json`（不静默丢） |
| 未知键 | 留在 agent 行（可能是第三方插件写的），并打印提示 |

## 用法

```bash
python scripts/migrate_v3_split_config.py --dry-run   # 只看计划
python scripts/migrate_v3_split_config.py             # 真跑（自动备份 DB）
```

特性：**幂等**（已搬过的键在 agent 行里已不存在）、**dry-run**、**自动备份**、
**不覆盖**模块行已有值。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

AGENT_CONFIG_MODULE = "agent"


def plan_split(agent_config: dict) -> dict:
    """把一条 agent 配置算成「留本体 / 搬到各模块 / 丢弃」三份（纯函数，可测）。

    Args:
        agent_config: agent 行的配置字典。

    Returns:
        ``{"agent_remaining": dict, "modules": {name: {k: v}}, "dropped": dict, "unknown": [k]}``
    """
    from app.agent.config_partition import CORE_KEYS, DELETED_KEYS, MODULE_KEYS

    core = set(CORE_KEYS)
    deleted = set(DELETED_KEYS)
    key_to_module = {key: name for name, keys in MODULE_KEYS.items() for key in keys}

    remaining: dict = {}
    modules: dict[str, dict] = {}
    dropped: dict = {}
    unknown: list[str] = []

    # 三套旧 per-tool map 收敛成一份名单（删键前先把"关掉的那些"搬进 tools_disabled）
    LEGACY_TOOL_MAPS = ("system_tools_enabled", "module_tools_enabled", "mcp_tools_enabled")
    disabled_tools: list[str] = list((agent_config or {}).get("tools_disabled") or [])

    for key, value in (agent_config or {}).items():
        if key in core:
            remaining[key] = value
        elif key in deleted:
            dropped[key] = value
        elif key in key_to_module:
            modules.setdefault(key_to_module[key], {})[key] = value
        else:
            # 不在分区表里：可能是第三方插件写的键，留在 agent 行
            remaining[key] = value
            unknown.append(key)

    if disabled_tools:
        remaining["tools_disabled"] = sorted(set(str(n) for n in disabled_tools))

    return {
        "agent_remaining": remaining,
        "modules": modules,
        "dropped": dropped,
        "unknown": unknown,
    }


async def apply_split(
    service,
    bot_id: str,
    plan: dict,
    *,
    dry_run: bool,
    dropped_sink: dict | None = None,
) -> dict:
    """把计划落库。返回实际写入统计。"""
    from app.agent.config_partition import MODULE_KEYS  # noqa: F401 (文档用途)

    stats = {"modules": {}, "agent_keys": len(plan["agent_remaining"]), "dropped": len(plan["dropped"])}

    if dry_run:
        for module_name, moved in plan["modules"].items():
            stats["modules"][module_name] = len(moved)
        return stats

    for module_name, moved in plan["modules"].items():
        existing = service.get_module_config(module_name, bot_id) or {}
        # 模块已有值优先：用户在模块页新配的东西不能被旧 agent 值覆盖
        merged = {**moved, **existing}
        await service.save_module_config(module_name, bot_id, merged)
        stats["modules"][module_name] = len(moved)

    await service.save_module_config(AGENT_CONFIG_MODULE, bot_id, plan["agent_remaining"])

    if dropped_sink is not None and plan["dropped"]:
        dropped_sink[bot_id] = plan["dropped"]
    return stats


def _dump_dropped(path: Path, dropped: dict) -> None:
    if not dropped:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dropped, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="v3：agent 配置 108 键拆到模块行")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不写库")
    parser.add_argument("--no-backup", action="store_true", help="跳过数据库备份")
    args = parser.parse_args(argv)

    from app.core.settings import load_settings
    from app.infrastructure.config.config_service import ConfigService
    from app.infrastructure.persistence.database import Database

    settings = load_settings()
    db_path = Path(settings.db_path)
    if not args.dry_run and not args.no_backup and db_path.is_file():
        backup_dir = db_path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        target = backup_dir / f"{db_path.stem}.split-migration.{int(time.time())}{db_path.suffix}"
        shutil.copy2(db_path, target)
        print(f"[backup] {target}")

    import asyncio

    async def _run() -> int:
        db = Database(str(db_path))
        await db.connect()
        try:
            service = ConfigService(db, settings.project_root)
            await service.init()
            bot_ids = list((service.get_all_module_configs(AGENT_CONFIG_MODULE) or {}).keys())
            if not bot_ids:
                print("[skip] 没有任何 agent 配置行（新装环境无需迁移）")
                return 0

            dropped_all: dict = {}
            total = 0
            for bot_id in bot_ids:
                cfg = service.get_module_config(AGENT_CONFIG_MODULE, bot_id) or {}
                plan = plan_split(cfg)
                if not plan["modules"] and not plan["dropped"]:
                    continue
                stats = await apply_split(
                    service, bot_id, plan, dry_run=args.dry_run, dropped_sink=dropped_all
                )
                moved = sum(stats["modules"].values())
                total += moved
                prefix = "[dry-run] " if args.dry_run else ""
                print(
                    f"{prefix}账号 {bot_id}: 搬走 {moved} 键 → {stats['modules']}；"
                    f"丢弃 {stats['dropped']} 键；本体保留 {stats['agent_keys']} 键"
                )
                if plan["unknown"]:
                    print(f"  提示：{len(plan['unknown'])} 个未知键留在 agent 行（可能是第三方插件）")

            _dump_dropped(Path("logs/migrate_v3_dropped_keys.json"), dropped_all)
            print(f"[done] 共迁移 {total} 个键" + ("（dry-run）" if args.dry_run else ""))
            return 0
        finally:
            await db.close()

    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
