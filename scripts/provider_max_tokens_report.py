"""盘点 provider 模型实例里的 ``max_tokens`` 覆盖，标出"看起来是旧默认值"的行。

背景：这一层的历史包袱是"烘默认值"。三个地方各自把 1024 焊进用户配置：

- 后端 ``create_model`` 无条件写 ``max_tokens=1024``；
- 前端保存时无条件送 ``max_tokens: Number(...) || 1024``，编辑时 ``?? 1024`` 回填；
- ``migrate_legacy_models`` 迁移时同样烘 1024。

于是 DB 里的 ``config_json.max_tokens == 1024`` 有两种可能的来历：**用户真填的 1024**，
和**当年被烘进去的默认值**。事后无法区分，所以这个脚本只做**只读盘点**，不猜也不改：

- 列出每个模型的生效值 / 来源 / 是否等于历史默认值；
- 标出"模型值是 1024、且连接预设没设 max_tokens"的可疑行；
- 给出可复制的处理方式（把该模型的 max_tokens 清空 = 交回下一层）。

用法::

    python scripts/provider_max_tokens_report.py            # 只读盘点
    python scripts/provider_max_tokens_report.py --json     # 机器可读
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: 历史上被烘进去的默认值。只用于**标记**，不用自动清除。
LEGACY_BAKED_DEFAULT = 1024


def main() -> int:
    parser = argparse.ArgumentParser(description="盘点 provider 模型的 max_tokens 覆盖")
    parser.add_argument("--json", action="store_true", help="输出 JSON（供脚本消费）")
    parser.add_argument("--db", default="", help="数据库路径（默认 data/app.db）")
    args = parser.parse_args()

    import asyncio

    from app.agent.providers.policy import LAYER_MODEL, is_reasoning_model, plan_max_tokens
    from app.core.settings import Settings
    from app.infrastructure.config.config_service import ConfigService
    from app.infrastructure.persistence.database import Database

    settings = Settings()
    db_path = Path(args.db) if args.db else settings.db_path

    async def _load():
        # 只连库 + 初始化配置服务：不走 build_container（那会拉起模块/Bot，
        # 一个"只读盘点"的脚本没有理由把整个应用跑起来）。
        db = Database(db_path)
        await db.connect()
        service = ConfigService(db, settings.project_root)
        await service.init()
        return service

    service = asyncio.run(_load())
    presets = {p["id"]: p for p in service.list_provider_presets()}
    rows = []
    for model in service.list_provider_models():
        preset = presets.get(model.get("preset_id", "")) or {}
        preset_cfg = dict(preset.get("config", {}) or {})
        model_cfg = dict(model.get("config", {}) or {})
        merged = {**preset_cfg, **model_cfg}
        merged["model"] = model.get("model", "")
        plan = plan_max_tokens({**merged, "_max_tokens_origin": LAYER_MODEL if "max_tokens" in model_cfg else ""})

        own = model_cfg.get("max_tokens")
        preset_has = preset_cfg.get("max_tokens") not in (None, "")
        rows.append({
            "model_id": model.get("id", ""),
            "model": model.get("model", ""),
            "preset": preset.get("name", preset.get("id", "")),
            "model_max_tokens": own,
            "preset_max_tokens": preset_cfg.get("max_tokens"),
            "effective": plan.value,
            "origin": plan.origin,
            "notes": list(plan.notes),
            "reasoning": is_reasoning_model({"model": model.get("model", ""), "reasoning": model_cfg.get("reasoning")}),
            # 可疑：模型层有一个恰好等于历史默认值的值，而预设层根本没设 —— 
            # 这种情况 99% 是当年烘进去的，不是用户的选择
            "suspect_baked_default": (
                own == LEGACY_BAKED_DEFAULT and not preset_has
            ),
        })

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    if not rows:
        print("没有 provider 模型实例。")
        return 0

    print(f"共 {len(rows)} 个模型实例\n")
    width = max(len(r["model"]) for r in rows)
    for row in rows:
        flag = "  ⚠ 疑似旧默认值" if row["suspect_baked_default"] else ""
        print(
            f"{row['model']:<{width}}  "
            f"生效 {row['effective']:>7}  （来自{row['origin']}）"
            f"  模型层={row['model_max_tokens']!s:<6} 预设层={row['preset_max_tokens']!s:<6}"
            f"{'  [思考模型]' if row['reasoning'] else ''}{flag}"
        )
        for note in row["notes"]:
            print(f"{'':<{width}}    · {note}")

    suspects = [r for r in rows if r["suspect_baked_default"]]
    if suspects:
        print(
            f"\n有 {len(suspects)} 个模型的「最大输出 Token」恰好等于历史默认值 "
            f"{LEGACY_BAKED_DEFAULT}，且连接预设没设该值 —— 很可能是当年被烘进去的。\n"
            "处理方式（不自动执行，需你确认）：在「Provider 预设 → 模型」里把这些模型的"
            "「最大输出 Token」清空，它们就会跟随下一层（Agent 页 → 内置默认）。"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
