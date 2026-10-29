"""对话策略的纯解析逻辑（无 IO，可单测、可给 UI 预览复用）。

## 概念

「回不回 / 怎么回 / 谁能触发」分三层回答：

1. **身份（谁）**：全局 owner/manager 名册 + 群角色 + 用户例外；
2. **场所（在哪）**：``enabled``——这个群/私聊让不让它说话；
3. **规则（怎么回）**：触发方式、引用/@、频控、静默……

本模块只做第 2、3 层的**合并**：默认值（能力自身配置）
→ 账号层覆盖（``channel_id=""``）→ 群层覆盖（``channel_id=<群号>``），最具体者胜。
每个键都记录来源（``default`` / ``account`` / ``channel``），UI 据此显示继承链。

## 与 ``domain/binding.py`` 的关系

``bindings`` 解决"能力 × 作用域"的配置，但它的 channel 档**没有账号维度**
（主键是 capability + scope），无法表达"账号 A 在群 X 允许、账号 B 不允许"。
所以群级覆盖单独一张表（``channel_overrides``，含 account_id），解析在这里；
两者分工见 docs/permission-dialogue-policy-design.md §3。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

SOURCE_DEFAULT = "default"
SOURCE_ACCOUNT = "account"
SOURCE_SCENE = "scene"
SOURCE_CHANNEL = "channel"

#: 场景层（对应旧的 group_enable / private_enable）在 ``channel_overrides`` 里的 channel_id。
#: 用无法与真实群号冲突的字符串占位，避免"账号默认"与"群聊默认"混在同一行。
SCENE_GROUP = "__group__"
SCENE_PRIVATE = "__private__"
SCENES: tuple[str, ...] = (SCENE_GROUP, SCENE_PRIVATE)

#: 账号层/群层可覆盖的配置键白名单（capability → keys）。
#:
#: **必须显式声明**：否则又会变成"任意键可覆盖、没人知道生效的是谁"。
#: 未在名单里的键，写接口直接拒绝（``POLICY_KEY_NOT_OVERRIDABLE``）。
SCOPE_OVERRIDABLE: dict[str, tuple[str, ...]] = {
    "agent": (
        "trigger_mode",           # 触发方式：at/keyword/both/all/none（显式字段）
        "trigger_probability",    # mode=all 时的参与概率（0-100）
        "trigger_at",             # 旧字段：是否响应 @（保留兼容）
        "trigger_keyword",        # 旧字段：关键词触发列表
        "reply_cooldown",         # 同会话最小回复间隔（秒）
        "message_debounce_seconds",   # 消息抖动窗口
        "message_debounce_max_wait",  # 抖动最长等待
        "interrupt_enable",       # 新消息是否打断旧回复
        "quiet_hours",            # 免打扰时段（"23:00-08:00"，空=不启用）
    ),
}

#: 权限键同样按层覆盖：谁能触发 / 谁被排除
PERMISSION_KEYS: tuple[str, ...] = ("role_min", "user_mode", "user_list")

# ==================== 触发方式（trigger_mode） ====================
#
# 历史只有 ``trigger_at``（bool）+ ``trigger_keyword``（list）两个键，UI 只能表达
# "只 @ / 只关键词 / 两者 / 都不"。``trigger_mode`` 把四种组合显式成一个字段，并补上
# "所有消息按概率参与"（``all`` + ``trigger_probability``）。读取时旧键缺省会被折算，
# 所以旧配置/旧脚本不需要迁移即可继续工作。

TRIGGER_AT = "at"
TRIGGER_KEYWORD = "keyword"
TRIGGER_BOTH = "both"
TRIGGER_ALL = "all"
TRIGGER_NONE = "none"
TRIGGER_MODES: tuple[str, ...] = (TRIGGER_AT, TRIGGER_KEYWORD, TRIGGER_BOTH, TRIGGER_ALL, TRIGGER_NONE)
#: 没配置过触发方式时的默认（与"只回 @ 我"的产品口径一致）
DEFAULT_TRIGGER_MODE = TRIGGER_AT
DEFAULT_TRIGGER_PROBABILITY = 100


def trigger_mode_from(config: Mapping[str, Any] | None) -> str:
    """从配置里取值：显式 ``trigger_mode`` 优先，否则由旧键折算。

    - 显式值合法 → 直接用；
    - 否则 ``trigger_at`` + 非空 ``trigger_keyword`` → ``both``；
    - 只有 ``trigger_at`` → ``at``；只有关键词 → ``keyword``；
    - 两者都没有 → ``none``（= 群消息不会触发；与改造前一致）。
    """
    data = config if isinstance(config, Mapping) else {}
    explicit = str(data.get("trigger_mode") or "").strip().lower()
    if explicit in TRIGGER_MODES:
        return explicit
    keywords = data.get("trigger_keyword")
    has_keywords = isinstance(keywords, list) and any(str(item).strip() for item in keywords)
    has_at = bool(data.get("trigger_at", False))
    if has_at and has_keywords:
        return TRIGGER_BOTH
    if has_at:
        return TRIGGER_AT
    if has_keywords:
        return TRIGGER_KEYWORD
    # 完全没有配置过这两个键（例如新建账号的默认配置还没写）→ 产品默认「只回 @」
    if "trigger_at" not in data and "trigger_keyword" not in data:
        return DEFAULT_TRIGGER_MODE
    return TRIGGER_NONE


def trigger_probability_of(config: Mapping[str, Any] | None) -> int:
    """``all`` 模式的参与概率（0-100，非法值按 100 = 不额外限制）。"""
    data = config if isinstance(config, Mapping) else {}
    try:
        value = int(data.get("trigger_probability", DEFAULT_TRIGGER_PROBABILITY))
    except (TypeError, ValueError):
        return DEFAULT_TRIGGER_PROBABILITY
    return max(0, min(100, value))


def legacy_trigger_keys(mode: str) -> dict[str, Any]:
    """把 ``trigger_mode`` 折算回旧键（双写，让旧前端/脚本继续可读）。"""
    normalized = mode if mode in TRIGGER_MODES else DEFAULT_TRIGGER_MODE
    return {"trigger_at": normalized in (TRIGGER_AT, TRIGGER_BOTH, TRIGGER_ALL)}


def is_overridable(capability: str, key: str) -> bool:
    """该键是否允许在账号/群层覆盖。"""
    return key in SCOPE_OVERRIDABLE.get(str(capability), ())


def overridable_keys(capability: str) -> tuple[str, ...]:
    return SCOPE_OVERRIDABLE.get(str(capability), ())


# ==================== 免打扰时段（quiet_hours） ====================
#
# 格式：``"HH:MM-HH:MM"``（如 ``23:00-08:00``，允许跨零点）；空串 = 不启用。
# 这里只做纯计算：解析/合法性/是否命中；时区一律用本机时间（与其它配置口径一致）。


def _parse_hhmm(value: str) -> int | None:
    """``"HH:MM"`` → 当日分钟数；格式不对返回 None。"""
    text = str(value or "").strip()
    if len(text) != 5 or text[2] != ":":
        return None
    try:
        hour = int(text[:2])
        minute = int(text[3:])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour * 60 + minute


def valid_quiet_hours(window: object) -> bool:
    """免打扰时段是否合法（空串视为合法 = 不启用）。"""
    text = str(window or "").strip()
    if not text:
        return True
    parts = text.split("-")
    if len(parts) != 2:
        return False
    return _parse_hhmm(parts[0]) is not None and _parse_hhmm(parts[1]) is not None


def in_quiet_hours(window: object, now_minutes: int | None = None) -> bool:
    """当前时刻是否落在免打扰时段内。

    ``now_minutes`` 可注入（测试/预览用）；缺省取本机当前时间。
    起止相同视为空窗口（不静默）；非法格式按"不静默"处理（否则写错一次就永久沉默）。
    """
    text = str(window or "").strip()
    if not text or not valid_quiet_hours(text):
        return False
    if now_minutes is None:
        from datetime import datetime

        now = datetime.now()
        now_minutes = now.hour * 60 + now.minute
    start_text, end_text = text.split("-")
    start = _parse_hhmm(start_text)
    end = _parse_hhmm(end_text)
    if start is None or end is None or start == end:
        return False
    current = int(now_minutes) % (24 * 60)
    if start < end:
        return start <= current < end
    # 跨零点：23:00-08:00 = [23:00, 24:00) ∪ [00:00, 08:00)
    return current >= start or current < end


# ==================== 覆盖值校验 ====================


def validate_override_values(config: Mapping[str, Any] | None) -> list[str]:
    """校验一批覆盖值，返回人类可读错误（空列表 = 合法）。

    白名单只解决"哪些键能覆盖"，不解决"值长什么样"；这里补上后者，
    避免把 ``quiet_hours="晚上"`` 这类值写进库、运行时静默失效。
    """
    errors: list[str] = []
    for key, value in (config or {}).items():
        if key == "quiet_hours":
            if not valid_quiet_hours(value):
                errors.append("免打扰时段格式应为 HH:MM-HH:MM（如 23:00-08:00）")
        elif key in ("trigger_at", "interrupt_enable"):
            if not isinstance(value, bool):
                errors.append(f"「{key}」应为 true/false")
        elif key == "trigger_mode":
            if str(value).strip().lower() not in TRIGGER_MODES:
                errors.append("触发方式只能是：" + "、".join(TRIGGER_MODES))
        elif key == "trigger_probability":
            if isinstance(value, bool) or not isinstance(value, int) or not (0 <= value <= 100):
                errors.append("参与概率应为 0-100 的整数")
        elif key == "trigger_keyword":
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                errors.append("触发关键词应为字符串数组")
            elif any(not item.strip() for item in value):
                errors.append("触发关键词不能为空字符串")
        elif key in ("reply_cooldown", "message_debounce_seconds", "message_debounce_max_wait"):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) < 0:
                errors.append(f"「{key}」应为 ≥0 的数字")
    return errors


@dataclass(frozen=True)
class ResolvedPolicy:
    """某账号在某群（或私聊/默认层）的生效策略。"""

    capability: str
    account_id: str
    channel_id: str | None
    enabled: bool
    enabled_source: str
    config: dict[str, Any]
    permission: dict[str, Any]
    #: 配置键 → 来源（default/account/channel）；UI 显示继承链用
    sources: Mapping[str, str] = field(default_factory=dict)

    def source_of(self, key: str) -> str:
        return str(self.sources.get(key, SOURCE_DEFAULT))


def _filter_keys(capability: str, data: Mapping[str, Any] | None) -> dict[str, Any]:
    """只保留白名单里的配置键（脏数据/旧键不进解析结果）。"""
    if not isinstance(data, Mapping):
        return {}
    return {key: value for key, value in data.items() if is_overridable(capability, key)}


def resolve_policy(
    capability: str,
    *,
    account_id: object,
    channel_id: object | None = None,
    defaults: Mapping[str, Any] | None = None,
    default_enabled: bool = True,
    account_row: Mapping[str, Any] | None = None,
    scene_row: Mapping[str, Any] | None = None,
    channel_row: Mapping[str, Any] | None = None,
) -> ResolvedPolicy:
    """按「默认 → 账号 → 场景 → 群」合并出生效策略。

    Args:
        capability: ``agent`` 或模块名（决定白名单）。
        account_id: 账号 id。
        channel_id: 群号；``None`` 表示私聊/默认层解析。
        defaults: 能力自身配置（agent: ``runtime.config``；模块: 模块配置）。
        default_enabled: 没有 enabled 覆盖时的默认开关。
        account_row: 账号层覆盖（对所有场景生效，含 ``user_mode/user_list``）。
        scene_row: 场景层覆盖（群聊 / 私聊各一份；对应旧的
            ``group_enable/private_enable``）。
        channel_row: 群层覆盖。
    """
    config: dict[str, Any] = _filter_keys(capability, defaults)
    sources: dict[str, str] = {key: SOURCE_DEFAULT for key in config}
    permission: dict[str, Any] = {}
    enabled = bool(default_enabled)
    enabled_source = SOURCE_DEFAULT

    layers = (
        (account_row, SOURCE_ACCOUNT),
        (scene_row, SOURCE_SCENE),
        (channel_row, SOURCE_CHANNEL),
    )
    for row, source in layers:
        if not isinstance(row, Mapping):
            continue
        if row.get("enabled") is not None:
            enabled = bool(row["enabled"])
            enabled_source = source
        for key, value in _filter_keys(capability, row.get("config")).items():
            config[key] = value
            sources[key] = source
        row_permission = row.get("permission")
        if isinstance(row_permission, Mapping):
            for key in PERMISSION_KEYS:
                if key in row_permission:
                    permission[key] = row_permission[key]
                    sources[key] = source

    # 派生字段：让 UI / 运行时只读 ``trigger_mode`` 一个字段，不必各自折算旧键。
    if "trigger_mode" not in config:
        config["trigger_mode"] = trigger_mode_from(config)
        derived_from = (
            sources.get("trigger_at") or sources.get("trigger_keyword") or SOURCE_DEFAULT
        )
        sources["trigger_mode"] = derived_from
    if "trigger_probability" not in config:
        config["trigger_probability"] = DEFAULT_TRIGGER_PROBABILITY
        sources["trigger_probability"] = SOURCE_DEFAULT

    return ResolvedPolicy(
        capability=str(capability),
        account_id=str(account_id),
        channel_id=None if channel_id in (None, "") else str(channel_id),
        enabled=enabled,
        enabled_source=enabled_source,
        config=config,
        permission=permission,
        sources=sources,
    )
