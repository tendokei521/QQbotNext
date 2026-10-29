"""启动迁移：把"未选提示词方案"的存量账号落到默认方案（chat）。

产品口径已改为「必须使用三种系统提示词之一」（见 `app/agent/assembly/presets.py`）：
新账号由 `DEFAULT_LLM_CONFIG["prompt_preset"] = "chat"` 兜底，存量账号里显式存着的
空串/拼错值需要一次改写，否则它们仍走"没有行为规则块"的旧文案。

幂等：已是三套之一（chat/roleplay/assistant）的账号不写。
"""

from __future__ import annotations

from typing import Any

from app.agent.assembly import presets as prompt_presets

AGENT_CAPABILITY = "agent"


async def migrate_prompt_preset(config_service: Any) -> int:
    """返回被改写的账号数（启动时 best-effort 调用）。"""
    known = set(prompt_presets.keys())
    updated = 0
    try:
        configs = dict(config_service.get_all_module_configs(AGENT_CAPABILITY) or {})
    except Exception:  # noqa: BLE001 —— 读不到就跳过，不阻断启动
        return 0
    for account_id, config in configs.items():
        current = str((config or {}).get("prompt_preset") or "").strip().lower()
        if current in known:
            continue
        new_config = dict(config or {})
        new_config["prompt_preset"] = prompt_presets.DEFAULT_PRESET_KEY
        try:
            await config_service.save_module_config(AGENT_CAPABILITY, account_id, new_config)
            updated += 1
        except Exception as e:  # noqa: BLE001 —— 单个账号失败不影响其它账号，但要留痕
            import logging

            logging.getLogger(__name__).warning(
                f"[PromptPreset] 账号 {account_id} 落默认方案失败（已跳过）: {e}"
            )
            continue
    return updated
