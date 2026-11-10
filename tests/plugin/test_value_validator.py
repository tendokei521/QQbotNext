"""P4-1：配置值校验器（纯函数）。

锁定：类型/范围/枚举/数组项都挡住；未知键只在导入路径拒绝（人工保存保持兼容）。
"""

from __future__ import annotations

from app.plugin.value_validator import (
    validate_config,
    validate_field,
    validate_permission_modes,
)

SCHEMA = {
    "reply_cooldown": {"type": "number", "min": 0, "max": 300, "label": "冷却"},
    "temperature": {"type": "float", "min": 0, "max": 2},
    "enabled": {"type": "boolean"},
    "mode": {"type": "select", "options": {"a": "A", "b": "B"}},
    "keywords": {"type": "string_list"},
    "count": {"type": "integer", "min": 1},
    "prompt": {"type": "textarea", "max_length": 5},
    "pools": {"type": "dynamic"},
    "image": {"type": "resource"},
    # ListWidget 字段：落库是 {<id>: {enabled, index}}（api-contract §5.1）
    "target_groups": {
        "type": "list",
        "label": "目标群聊",
        "endpoint": "groups",
        "id_field": "group_id",
        "name_field": "group_name",
        "checkboxes": True,
        "default": {},
    },
    "cards": {"type": "repeater"},
}


def test_valid_values_pass():
    issues = validate_config(SCHEMA, {
        "reply_cooldown": 3,
        "temperature": 0.7,
        "enabled": True,
        "mode": "a",
        "keywords": ["你好", "hi"],
        "count": 2,
        "prompt": "短句",
        "pools": {"k": 1},
        "image": "img-1",
    })
    assert issues == []


def test_wrong_types_are_rejected():
    assert validate_field("enabled", SCHEMA["enabled"], "true")[0]["key"] == "enabled"
    assert validate_field("reply_cooldown", SCHEMA["reply_cooldown"], "3")
    assert validate_field("count", SCHEMA["count"], 1.5)
    assert validate_field("keywords", SCHEMA["keywords"], ["ok", ""])


def test_ranges_and_enums():
    assert validate_field("reply_cooldown", SCHEMA["reply_cooldown"], -1)
    assert validate_field("reply_cooldown", SCHEMA["reply_cooldown"], 999)
    assert validate_field("temperature", SCHEMA["temperature"], 3)
    assert validate_field("mode", SCHEMA["mode"], "c")
    assert validate_field("prompt", SCHEMA["prompt"], "实在是太长了")


def test_unknown_keys_only_rejected_on_import_path():
    assert validate_config(SCHEMA, {"unknown_key": 1}) == []
    issues = validate_config(SCHEMA, {"unknown_key": 1}, reject_unknown=True)
    assert issues and issues[0]["key"] == "unknown_key"


def test_list_type_accepts_listwidget_object():
    """回归：ListWidget 保存 {<id>: {enabled, index}} 曾被当成「应为数组」拒绝。

    现场症状（防撤回 target_groups / target_users）：
    「配置值不合法，未保存：target_groups: 「目标群聊」应为数组；…」
    """
    payload = {
        "target_groups": {"123456": {"enabled": True, "index": 0}, "654321": {"enabled": False, "index": 1}},
    }
    assert validate_config(SCHEMA, payload) == []
    # 空对象（模块没配过、前端原样回传）同样合法
    assert validate_config(SCHEMA, {"target_groups": {}}) == []
    # 旧版数组格式（resolve_enabled_ids 仍兼容）继续接受
    assert validate_config(SCHEMA, {"target_groups": ["123456"]}) == []


def test_list_type_still_rejects_scalars():
    for bad in ("123456", 123456, True):
        issues = validate_field("target_groups", SCHEMA["target_groups"], bad)
        assert issues and "目标群聊" in issues[0]["message"]


def test_repeater_still_requires_array():
    assert validate_field("cards", SCHEMA["cards"], [{"a": 1}]) == []
    assert validate_field("cards", SCHEMA["cards"], {"a": 1})[0]["key"] == "cards"


def test_permission_modes():
    assert validate_permission_modes("whitelist", "blacklist") == []
    bad = validate_permission_modes("allow-all", "黑名单")
    assert {item["key"] for item in bad} == {"group_mode", "user_mode"}
