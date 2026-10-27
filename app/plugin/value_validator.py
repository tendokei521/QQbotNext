"""配置值校验器：把"必然跑不通/会静默失效"的值挡在写库之前。

为什么需要（P4-1）：现有保存路径只对 ``password`` 哨兵与资源 ``on_missing`` 做特判，
类型/范围/枚举一律接受。人改错自己认，**机器导入别人的配置写错了就是静默垃圾数据**。

这里只做纯函数校验（不读业务状态），返回人类可读的 issue 列表：
``[{"key": "reply_cooldown", "message": "应为 ≥0 的数字"}]``。
"""

from __future__ import annotations

from typing import Any

#: 允许的权限模式（旧接口曾接受任意字符串，非法值在 authority 里"两个分支都不进"= 放行）
PERMISSION_MODES = ("whitelist", "blacklist")

_SIMPLE_STR_TYPES = ("text", "string", "password", "textarea")
_NUMBER_TYPES = ("number", "integer", "float")
_BOOL_TYPES = ("boolean", "bool")


def _issue(key: str, message: str) -> dict:
    return {"key": key, "message": message}


def validate_field(key: str, field: dict, value: Any) -> list[dict]:
    """校验单个字段值；未知类型只做"非空对象"兜底，不误杀新类型。"""
    if not isinstance(field, dict):
        return []
    ftype = str(field.get("type") or "text").strip().lower()

    if ftype in _SIMPLE_STR_TYPES:
        if not isinstance(value, str):
            return [_issue(key, f"「{field.get('label', key)}」应为字符串")]
        max_len = field.get("max_length")
        if isinstance(max_len, int) and max_len > 0 and len(value) > max_len:
            return [_issue(key, f"「{field.get('label', key)}」超过 {max_len} 字")]
        return []

    if ftype in _NUMBER_TYPES:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return [_issue(key, f"「{field.get('label', key)}」应为数字")]
        if ftype == "integer" and not isinstance(value, int):
            return [_issue(key, f"「{field.get('label', key)}」应为整数")]
        minimum = field.get("min")
        maximum = field.get("max")
        if isinstance(minimum, (int, float)) and value < minimum:
            return [_issue(key, f"「{field.get('label', key)}」不能小于 {minimum}")]
        if isinstance(maximum, (int, float)) and value > maximum:
            return [_issue(key, f"「{field.get('label', key)}」不能大于 {maximum}")]
        return []

    if ftype in _BOOL_TYPES:
        if not isinstance(value, bool):
            return [_issue(key, f"「{field.get('label', key)}」应为 true/false")]
        return []

    if ftype == "select":
        options = field.get("options") or field.get("items") or {}
        if isinstance(options, dict):
            allowed = set(str(item) for item in options.keys())
        elif isinstance(options, list):
            allowed = {str(item.get("value") if isinstance(item, dict) else item) for item in options}
        else:
            allowed = set()
        if allowed and str(value) not in allowed:
            return [_issue(key, f"「{field.get('label', key)}」只能是：{'、'.join(sorted(allowed))}")]
        return []

    if ftype == "string_list":
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            return [_issue(key, f"「{field.get('label', key)}」应为字符串数组")]
        if any(not item.strip() for item in value):
            return [_issue(key, f"「{field.get('label', key)}」不能包含空项")]
        return []

    if ftype in ("list", "repeater"):
        if not isinstance(value, list):
            return [_issue(key, f"「{field.get('label', key)}」应为数组")]
        return []

    if ftype == "dynamic":
        if not isinstance(value, dict):
            return [_issue(key, f"「{field.get('label', key)}」应为对象")]
        return []

    if ftype in ("resource", "resources"):
        if ftype == "resource" and not isinstance(value, str):
            return [_issue(key, f"「{field.get('label', key)}」应为资源 id")]
        if ftype == "resources" and (
            not isinstance(value, list) or any(not isinstance(item, str) for item in value)
        ):
            return [_issue(key, f"「{field.get('label', key)}」应为资源 id 数组")]
        return []

    return []


def validate_config(
    schema: dict | None,
    data: dict | None,
    *,
    reject_unknown: bool = False,
) -> list[dict]:
    """校验一份配置对象。

    ``reject_unknown=True`` 用于**导入**路径（未知键装进去也不会生效，必须拒绝）；
    人工保存路径保持 ``False``（历史配置里可能带模块旧键，前端会原样回传）。
    """
    if not isinstance(data, dict):
        return [_issue("", "配置必须是对象")]
    schema = schema or {}
    issues: list[dict] = []
    for key, value in data.items():
        field = schema.get(key)
        if field is None:
            if reject_unknown:
                issues.append(_issue(str(key), f"未知配置项：{key}"))
            continue
        issues.extend(validate_field(str(key), field, value))
    return issues


def validate_permission_modes(group_mode: str, user_mode: str) -> list[dict]:
    """模块权限的 group_mode / user_mode 必须是 whitelist|blacklist。

    历史坑：非法值在 `authority.check_module_permission` 里"两个分支都不进"，
    等价于**放行所有人**；保存时必须拒绝。
    """
    issues: list[dict] = []
    if str(group_mode) not in PERMISSION_MODES:
        issues.append(_issue("group_mode", "群组模式只能是 whitelist / blacklist"))
    if str(user_mode) not in PERMISSION_MODES:
        issues.append(_issue("user_mode", "用户模式只能是 whitelist / blacklist"))
    return issues
