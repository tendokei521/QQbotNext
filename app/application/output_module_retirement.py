"""`llm_output` 模块退役：把它的配置并入账号级 agent 配置。

2026-11-01：流式输出内置进框架（`delivery/output.py` + `delivery/pool.py`），
`llm_output` 模块被删除。它的参数原本存在 bindings 的模块配置里
（`capability_id='llm_output'`），现在归账号级 agent 配置，所以启动时搬一次。

幂等：搬完写 kv 标记，并删掉旧模块行。
`mode`（text/stream/speech）已废弃、不搬迁——传输方式由 `stream_output` 表达。
"""

from __future__ import annotations

from typing import Any

from app.core.logger import logger

#: 已退役的模块 id
RETIRED_MODULE = "llm_output"

#: 迁移标记（写进 kv，值为 "1"）
MIGRATION_FLAG = "llm_output_retired_v1"

#: 需要搬走的键（现在都归 agent 配置）
MIGRATED_KEYS: tuple[str, ...] = (
    "stream_output",
    "stream_send_pool_enabled",
    "stream_send_by_sentence",
    "stream_sentence_max_length",
    "stream_send_interval_mode",
    "stream_send_interval_base_ms",
    "stream_send_interval_min_ms",
    "stream_send_interval_max_ms",
    "stream_send_curve",
    "stream_send_curve_k",
    "stream_short_message_length",
    "stream_short_message_delay_ms",
    "stream_long_message_delay_ms",
    "stream_send_prefix",
    "stream_send_suffix",
    "stream_send_max_queue",
    "stream_queue_full_policy",
    "stream_flush_on_finish",
    "stream_keep_order",
    "speech_max_bubbles",
    "speech_max_bubble_chars",
)


async def migrate_llm_output_config(config_service: Any) -> dict:
    """把 `llm_output` 的模块配置并入账号级 agent 配置，然后删掉旧行。

    Returns:
        ``{"rows": 搬了几个作用域, "keys": 搬了几个键, "errors": [...]}``
    """
    report: dict[str, Any] = {"rows": 0, "keys": 0, "errors": []}
    try:
        if await config_service.get_kv(MIGRATION_FLAG, "") == "1":
            return report
    except Exception as e:  # noqa: BLE001 —— 读不到标记就按未迁移处理
        logger.warning(f"[Migrate] llm_output 退役：读迁移标记失败（按未迁移处理）: {e}")

    rows = config_service.list_bindings(RETIRED_MODULE)
    if not rows:
        # 新装环境 / 已经删干净：不写标记（之后不会再有这个模块的行）
        return report

    for row in rows:
        scope_type = str(row.get("scope_type") or "account")
        scope_id = str(row.get("scope_id") or "")
        cfg = row.get("config") if isinstance(row.get("config"), dict) else {}
        moved = {k: v for k, v in cfg.items() if k in MIGRATED_KEYS and v is not None}
        try:
            if moved and scope_type == "account" and scope_id:
                existing = config_service.get_binding("agent", scope_type, scope_id) or {}
                base = existing.get("config") if isinstance(existing.get("config"), dict) else {}
                # 模块里的值是用户实际调过的，优先于 agent 的默认值
                fields: dict[str, Any] = {
                    "enabled": bool(existing.get("enabled", True)),
                    "config": {**base, **moved},
                }
                await config_service.save_binding("agent", scope_type, scope_id, **fields)
            await config_service.db.execute(
                "DELETE FROM bindings WHERE capability_id=? AND scope_type=? AND scope_id=?",
                (RETIRED_MODULE, scope_type, scope_id),
            )
            report["rows"] += 1
            report["keys"] += len(moved)
        except Exception as e:  # noqa: BLE001 —— 单作用域失败不影响其它
            report["errors"].append(f"{scope_type}:{scope_id} {e}")
            logger.error(f"[Migrate] llm_output 退役失败（{scope_type}:{scope_id}）: {e}")

    reloader = getattr(config_service, "_load_bindings", None)
    if callable(reloader):
        await reloader()

    if not report["errors"]:
        await config_service.set_kv(MIGRATION_FLAG, "1")
        logger.info(
            f"[Migrate] llm_output 模块已退役：{report['rows']} 个作用域、"
            f"{report['keys']} 个配置键并入 agent 配置"
        )
    else:
        logger.error(f"[Migrate] llm_output 退役有 {len(report['errors'])} 个错误（下次启动重试）")
    return report


__all__ = ["MIGRATED_KEYS", "MIGRATION_FLAG", "RETIRED_MODULE", "migrate_llm_output_config"]
