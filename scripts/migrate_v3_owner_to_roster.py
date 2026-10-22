"""v3 配置迁移：账号级 owner_id → Agent 配置的拥有者名册。

## 为什么需要

v3 起**不再有"账号级 owner QQ"**：拥有者身份的唯一来源是
Agent 配置里的 ``agent_owner_list``（权限管理页）。

老用户的 ``bots.owner_id`` 若直接丢弃，会导致：
- ``permission = "owner"`` 的模块不再响应（权限过滤是静默的，表现为"模块一声不响"）；
- ``#llm`` 里仅拥有者可用的指令（``stop`` / ``memory audit``）全部失效。

所以迁移必须**先并入名册，再置空账号级字段**。

## 用法

```bash
python scripts/migrate_v3_owner_to_roster.py --dry-run   # 只看要写什么
python scripts/migrate_v3_owner_to_roster.py             # 真跑（自动备份 DB）
```

特性：**幂等**（已在名册里的不重复追加）、**不覆盖**（只追加）、**留有证据**
（打印每个账号的 before/after 名册）。
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

AGENT_CONFIG_MODULE = "agent"


def _normalise(roster) -> list[str]:
    """把名册归一成去空、去重的字符串列表（保序）。"""
    if not roster:
        return []
    if isinstance(roster, str):
        roster = roster.replace(",", "\n").splitlines()
    out: list[str] = []
    for item in roster:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def plan_migration(bots: dict, get_agent_config, get_account_id) -> list[dict]:
    """计算迁移计划（纯函数，便于测试）。

    Args:
        bots: ``{index: {"ws_url": ..., "owner_id": ...}}``（config_service.get_bots()）。
        get_agent_config: ``callable(account_id) -> dict``，取该账号的 Agent 配置。
        get_account_id: ``callable(index, bot_cfg) -> str | None``，解析账号 QQ。

    Returns:
        ``[{"index", "account_id", "owner_id", "before", "after", "changed"}]``。
    """
    plan: list[dict] = []
    for index, cfg in (bots or {}).items():
        owner = (cfg or {}).get("owner_id")
        if owner in (None, "", 0):
            continue
        account_id = get_account_id(index, cfg)
        if not account_id:
            continue
        roster = _normalise((get_agent_config(account_id) or {}).get("agent_owner_list"))
        merged = list(roster)
        if str(owner) not in merged:
            merged.append(str(owner))
        plan.append(
            {
                "index": index,
                "account_id": str(account_id),
                "owner_id": str(owner),
                "before": roster,
                "after": merged,
                "changed": merged != roster,
            }
        )
    return plan


def apply_plan(plan: list[dict], get_agent_config, set_agent_config, clear_owner_id, *, dry_run: bool) -> int:
    """落库：把 owner 并入名册并清空账号级字段；返回实际改动条数。"""
    applied = 0
    for item in plan:
        if dry_run:
            print(
                f"[dry-run] 账号 {item['account_id']}: 名册 "
                f"{item['before']} → {item['after']}（追加 {item['owner_id']}）"
            )
            applied += 1
            continue
        cfg = dict(get_agent_config(item["account_id"]) or {})
        cfg["agent_owner_list"] = item["after"]
        set_agent_config(item["account_id"], cfg)
        clear_owner_id(item["index"])
        applied += 1
        print(f"[ok] 账号 {item['account_id']}: 名册 {item['after']}")
    return applied


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="v3：账号级 owner_id 并入拥有者名册")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不写库")
    parser.add_argument("--no-backup", action="store_true", help="跳过数据库备份（默认会备份）")
    args = parser.parse_args(argv)

    from app.core.settings import load_settings
    from app.infrastructure.config.config_service import ConfigService
    from app.infrastructure.persistence.database import Database

    settings = load_settings()
    db_path = Path(settings.db_path)
    if not args.dry_run and not args.no_backup and db_path.is_file():
        backup_dir = db_path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        target = backup_dir / f"{db_path.stem}.owner-migration.{int(time.time())}{db_path.suffix}"
        shutil.copy2(db_path, target)
        print(f"[backup] {target}")

    import asyncio

    async def _run() -> int:
        db = Database(str(db_path))
        await db.connect()
        try:
            service = ConfigService(db)
            await service.init()
            bots = service.get_bots() or {}
            accounts = service.get_bot_accounts() or {}

            def _account_id(index, cfg):
                info = accounts.get(index) or {}
                return info.get("user_id") or cfg.get("user_id")

            plan = plan_migration(
                bots,
                lambda account_id: service.get_module_config(AGENT_CONFIG_MODULE, account_id),
                _account_id,
            )
            if not plan:
                print("[done] 没有需要迁移的账号级 owner_id")
                return 0
            applied = apply_plan(
                plan,
                lambda account_id: service.get_module_config(AGENT_CONFIG_MODULE, account_id),
                lambda account_id, cfg: service.set_module_config(
                    AGENT_CONFIG_MODULE, account_id, cfg, persist=True
                ),
                lambda index: _clear_owner(index, service, bots),
                dry_run=args.dry_run,
            )
            print(f"[done] 处理 {applied} 个账号" + ("（dry-run）" if args.dry_run else ""))
            return 0
        finally:
            await db.close()

    return asyncio.run(_run())


def _clear_owner(index, service, bots: dict) -> None:
    """把账号级 owner_id 置空（保留列，SQLite 不删列）。"""
    cfg = dict((bots or {}).get(index) or {})
    if not cfg:
        return
    cfg["owner_id"] = None
    try:
        service.set_bots({index: cfg})
    except Exception as e:  # noqa: BLE001 —— 清空失败不影响名册已写入
        print(f"[warn] 清空账号 {index} 的 owner_id 失败（可忽略，已不再被读取）: {e}")


if __name__ == "__main__":
    raise SystemExit(main())
