"""三种系统提示词方案的可编辑文本（全局覆盖，存 kv）。

为什么单独一层：提示词方案（chat / roleplay / assistant）的正文原本是代码常量
（`app/agent/assembly/presets.py` + `app/agent/persona/rules.py`）。用户希望在设置里
本地改这三套「系统提示词」，就需要一个**不碰代码**的覆盖层：

- 覆盖文本按方案 key 存一份（全局，对所有账号生效）；
- 没覆盖时用代码里的默认渲染结果；
- 装配时优先用覆盖文本（账号级 `behavior_rules_override` 仍然优先于它）。

覆盖后不再按账号能力（视觉/搜索/记忆/发言方式）动态裁剪——文案变成静态文本，
这一点在设置页会明确提示，属于"高级编辑"的取舍。
"""

from __future__ import annotations

from typing import Any

from app.agent.assembly import presets as prompt_presets

#: kv 键：``{preset_key: 覆盖文本}``
KV_KEY = "prompt_preset_overrides"
#: 单套文本上限（防止误粘贴整份文档）
MAX_TEXT_CHARS = 20000

_OVERRIDES: dict[str, str] = {}


class PromptPresetError(ValueError):
    """方案 key 未知 / 文本超长。"""


def get_override(key: str) -> str:
    """当前生效的覆盖文本（同步读缓存，供装配热路径使用）。"""
    return _OVERRIDES.get(str(key or "").strip().lower(), "")


def overrides_snapshot() -> dict[str, str]:
    return dict(_OVERRIDES)


def _default_text(preset) -> str:
    """代码里的默认渲染结果（无运行时、无能力裁剪的最小形态）。"""
    try:
        return prompt_presets.render(
            preset, runtime=None, available_tools=None, config=None, modalities=[]
        )
    except Exception:  # noqa: BLE001 —— 默认文本渲染失败不该让设置页 500
        return ""


async def load_overrides(config_service: Any) -> dict[str, str]:
    """启动时从 kv 读入缓存（幂等）。"""
    global _OVERRIDES
    raw = {}
    if config_service is not None:
        try:
            raw = await config_service.get_kv(KV_KEY, {}) or {}
        except Exception:  # noqa: BLE001 —— 读失败按"全部默认"处理
            raw = {}
    _OVERRIDES = {
        str(key).strip().lower(): str(value)
        for key, value in raw.items()
        if isinstance(key, str) and isinstance(value, str) and str(value).strip()
    }
    return dict(_OVERRIDES)


def describe_presets() -> list[dict]:
    """三套方案的默认文本 + 当前覆盖 + 实际生效文本（供设置页编辑）。"""
    rows: list[dict] = []
    for preset in prompt_presets.PRESETS:
        default_text = _default_text(preset)
        override = get_override(preset.key)
        rows.append({
            "key": preset.key,
            "label": preset.label,
            "description": preset.description,
            "default_text": default_text,
            "override_text": override,
            "effective_text": override or default_text,
            "customized": bool(override),
        })
    return rows


async def save_override(config_service: Any, key: str, text: str) -> dict:
    """保存一套方案的覆盖文本；``text`` 为空 = 恢复默认。"""
    preset = prompt_presets.get(key)
    if preset is None:
        raise PromptPresetError(f"没有这套提示词方案：{key or '(空)'}")
    cleaned = str(text or "").strip()
    if len(cleaned) > MAX_TEXT_CHARS:
        raise PromptPresetError(f"文本太长（上限 {MAX_TEXT_CHARS} 字）")

    global _OVERRIDES
    data = dict(_OVERRIDES)
    if cleaned:
        data[preset.key] = cleaned
    else:
        data.pop(preset.key, None)
    await config_service.set_kv(KV_KEY, data)
    _OVERRIDES = data
    return {
        "key": preset.key,
        "customized": bool(cleaned),
        "effective_text": cleaned or _default_text(preset),
    }


async def reset_override(config_service: Any, key: str) -> dict:
    return await save_override(config_service, key, "")
