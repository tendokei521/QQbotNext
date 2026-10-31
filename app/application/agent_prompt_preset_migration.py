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


async def reconcile_persona_copy(config_service: Any) -> int:
    """把账号里那份 ``system_prompt`` 副本对齐到它绑定的人格预设（启动时 best-effort）。

    ## 为什么会有两份

    应用人格时前端同时写 ``persona_preset_id`` 与 ``system_prompt``（后者是历史遗留的
    "内容快照"）。装配时**预设优先、副本只在预设缺失时兜底**，于是用户在预设里改了文案，
    账号里的副本还停在旧内容：

    - 改预设 → 生效（对，直接用预设）；
    - 改账号级 ``system_prompt`` → **不生效**（被预设压着），但 UI 读的是副本，
      看起来"改了没反应"；
    - 预设一删，旧副本立刻顶上（"过时的提示词又回来了"）。

    这里把副本同步成预设当前内容，消除分叉；返回被修正的账号数。
    **不动**绑定关系、不动没有预设的账号（那种情况副本就是唯一来源）。
    """
    try:
        configs = dict(config_service.get_all_module_configs(AGENT_CAPABILITY) or {})
    except Exception:  # noqa: BLE001 —— 读不到就跳过，不阻断启动
        return 0
    fixed = 0
    for account_id, config in configs.items():
        data = dict(config or {})
        preset_id = str(data.get("persona_preset_id") or "").strip()
        if not preset_id:
            continue
        try:
            preset = config_service.get_role_preset(preset_id)
        except Exception as e:  # noqa: BLE001 —— 单个账号读失败不影响其它账号
            _log_warning(f"[Persona] 账号 {account_id} 读取人格预设失败（跳过对齐）: {e}")
            continue
        if not isinstance(preset, dict):
            # 预设已删：保留副本（它是兜底来源），留给用户重新选人格
            _log_warning(
                f"[Persona] 账号 {account_id} 绑定的人格预设 {preset_id} 已不存在，"
                "继续使用账号内副本；建议在「人格设定」里重新选择"
            )
            continue
        want = str(preset.get("system_prompt") or "")
        current = str(data.get("system_prompt") or "")
        if want.strip() == current.strip():
            continue
        data["system_prompt"] = want
        try:
            await config_service.save_module_config(AGENT_CAPABILITY, account_id, data)
            fixed += 1
        except Exception as e:  # noqa: BLE001 —— 单账号失败只留痕
            _log_warning(f"[Persona] 账号 {account_id} 副本对齐失败（已跳过）: {e}")
    if fixed:
        _log_warning(
            f"[Persona] 已把 {fixed} 个账号的 system_prompt 副本对齐到绑定的人格预设"
            "（副本只在未选人格时兜底，避免「改了预设/改了副本不生效」的错觉）"
        )
    return fixed


def _log_warning(message: str) -> None:
    import logging

    logging.getLogger(__name__).warning(message)
