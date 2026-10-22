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
# ① 自动：从「上次登录账号」快照里推出账号 QQ，把旧 owner_id 并入名册
python scripts/migrate_v3_owner_to_roster.py --dry-run
python scripts/migrate_v3_owner_to_roster.py

# ② 手动：快照缺失（新机器/刚换号）时直接指定谁的账号加谁
python scripts/migrate_v3_owner_to_roster.py --bot-id 3437542570 --owner-id 10001
```

特性：**幂等**（已在名册里的不重复追加）、**不覆盖**（只追加）、**dry-run**、
**自动备份 DB**、无话可说时打印原因（不静默成功）。
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


def _merge(roster: list[str], owner: str) -> list[str]:
    merged = list(roster)
    if str(owner).strip() and str(owner).strip() not in merged:
        merged.append(str(owner).strip())
    return merged


def plan_migration(bots, get_agent_config, get_account_id) -> list[dict]:
    """计算迁移计划（纯函数，便于测试）。

    Args:
        bots: ``{index: {"ws_url": ..., "owner_id": ...}}`` 或等价的 list（按位置建索引）。
        get_agent_config: ``callable(account_id) -> dict``，取该账号的 Agent 配置。
        get_account_id: ``callable(index, bot_cfg) -> str | None``，解析账号 QQ。

    Returns:
        ``[{"index", "account_id", "owner_id", "before", "after", "changed"}]``。
    """
    if not isinstance(bots, dict):
        bots = dict(enumerate(bots or []))
    plan: list[dict] = []
    for index, cfg in bots.items():
        owner = (cfg or {}).get("owner_id")
        if owner in (None, "", 0):
            continue
        account_id = get_account_id(index, cfg)
        if not account_id:
            continue
        roster = _normalise((get_agent_config(account_id) or {}).get("agent_owner_list"))
        merged = _merge(roster, owner)
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


def build_manual_plan(bot_id: str, owner_id: str, get_agent_config) -> list[dict]:
    """手动模式：不依赖账号快照，直接为指定账号追加指定 owner。"""
    bot_id = str(bot_id or "").strip()
    owner_id = str(owner_id or "").strip()
    if not bot_id or not owner_id:
        raise ValueError("--bot-id 与 --owner-id 都必须给出")
    roster = _normalise((get_agent_config(bot_id) or {}).get("agent_owner_list"))
    merged = _merge(roster, owner_id)
    return [
        {
            "index": None,
            "account_id": bot_id,
            "owner_id": owner_id,
            "before": roster,
            "after": merged,
            "changed": merged != roster,
        }
    ]


def apply_plan(
    plan: list[dict], get_agent_config, set_agent_config, clear_owner_id, *, dry_run: bool
) -> int:
    """落库：把 owner 并入名册；`clear_owner_id(index)` 收集需要置空的账号。

    Returns:
        实际处理的条目数。
    """
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
        if item["index"] is not None:
            clear_owner_id(item["index"])
        applied += 1
        print(f"[ok] 账号 {item['account_id']}: 名册 {item['after']}")
    return applied


async def apply_plan_async(
    plan: list[dict], get_agent_config, set_agent_config, clear_owner_id, *, dry_run: bool
) -> int:
    """`apply_plan` 的异步版：``set_agent_config`` 可能是协程（真实持久化要 await）。"""
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
        result = set_agent_config(item["account_id"], cfg)
        if hasattr(result, "__await__"):
            await result
        if item["index"] is not None:
            clear_owner_id(item["index"])
        applied += 1
        print(f"[ok] 账号 {item['account_id']}: 名册 {item['after']}")
    return applied


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="v3：账号级 owner_id 并入拥有者名册")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不写库")
    parser.add_argument("--no-backup", action="store_true", help="跳过数据库备份（默认会备份）")
    parser.add_argument("--bot-id", help="手动模式：目标账号 QQ（Bot 自己）")
    parser.add_argument("--owner-id", help="手动模式：要加入拥有者名册的 QQ")
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
            service = ConfigService(db, settings.project_root)
            await service.init()
            bots_list = service.get_bots() or []
            accounts = service.get_bot_accounts() or {}

            def _get_cfg(account_id):
                return service.get_module_config(AGENT_CONFIG_MODULE, account_id) or {}

            async def _set_cfg(account_id, cfg):
                # 必须 await：`set_module_config(persist=True)` 只是**调度**异步写，
                # 脚本紧接着就 db.close()，会得到 "Cannot operate on a closed database"。
                await service.save_module_config(AGENT_CONFIG_MODULE, account_id, cfg)

            def _account_id(index, cfg):
                info = accounts.get(index) or {}
                return info.get("user_id") or (cfg or {}).get("user_id")

            if args.bot_id or args.owner_id:
                plan = build_manual_plan(args.bot_id, args.owner_id, _get_cfg)
                cleared: list[int] = []
            else:
                plan = plan_migration(dict(enumerate(bots_list)), _get_cfg, _account_id)
                cleared = []

            if not plan:
                _explain_empty(bots_list, accounts)
                return 1

            applied = await apply_plan_async(
                plan,
                _get_cfg,
                _set_cfg,
                cleared.append,
                dry_run=args.dry_run,
            )

            # 账号级 owner_id 置空：`save_bots` 是整表写入，所以统一改完再落一次
            if cleared and not args.dry_run:
                for index in cleared:
                    if 0 <= index < len(bots_list):
                        bots_list[index] = {**bots_list[index], "owner_id": None}
                await service.save_bots(bots_list)
                print(f"[ok] 已清空 {len(cleared)} 个账号的旧 owner_id")

            print(f"[done] 处理 {applied} 个账号" + ("（dry-run）" if args.dry_run else ""))
            if not args.dry_run:
                print("[next] 打开 WebUI → 权限管理 → 拥有者，确认你的 QQ 已在名册里")
            return 0
        finally:
            await db.close()

    return asyncio.run(_run())


def _explain_empty(bots_list: list[dict], accounts: dict) -> None:
    """一条都没迁到时说清原因，不静默"成功"。"""
    has_owner = [i for i, b in enumerate(bots_list) if (b or {}).get("owner_id")]
    print("[skip] 没有可迁移的账号级 owner_id")
    if not bots_list:
        print("  原因：配置里还没有任何账号（先去 WebUI 账号页加一个 ws 地址并登录一次）")
        return
    if not has_owner:
        print("  原因：所有账号的 owner_id 都是空的 —— 已经是 v3 形态，无需迁移")
        print("  若仍有模块不响应，请手动在「权限管理 → 拥有者」填你的 QQ")
        return
    missing = [i for i in has_owner if not (accounts.get(i) or {}).get("user_id")]
    if missing:
        print(f"  原因：索引 {missing} 有 owner_id 但拿不到「上次登录账号」（从未登录过？）")
        print("  解决：先让该账号登录一次，或用手动模式：")
        for i in missing:
            print(
                f"    python scripts/migrate_v3_owner_to_roster.py "
                f"--bot-id <Bot的QQ> --owner-id {(bots_list[i] or {}).get('owner_id')}"
            )


if __name__ == "__main__":
    raise SystemExit(main())
