"""OneBot 工具清单的**数据文件**约束（`app/llm/onebot_tools/manifest.json`）。

清单原本是 4,792 行的 Python 字面量，现已外置为 JSON。外置的收益只有在
「数据被当作数据校验」时才成立，所以这里守住四件事：

1. 结构合法：必填字段、类型、枚举、唯一性——坏条目在 CI 就红，而不是等运行时；
2. `sensitivity` 不进数据文件：它是派生字段，策略（`_CRITICAL_TOOLS` /
   `_HIGH_TOOLS`）必须留在代码里，一处可审计；
3. 策略名单不悬空：名单里提到的工具必须在清单里真实存在（防拼写错误静默失效）；
4. 加载器保持薄：`manifest.py` 一旦重新长出大段字面量，这条测试会失败。

语义约束（doc_url 形态、工具命名、action 别名）见 `tests/test_llm_onebot_tools.py`。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agent.tools.onebot import manifest as manifest_module

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_JSON = REPO_ROOT / "app" / "agent" / "tools" / "onebot" / "manifest.json"
MANIFEST_PY = REPO_ROOT / "app" / "agent" / "tools" / "onebot" / "manifest.py"

REQUIRED_FIELDS = {
    "name": str,
    "description": str,
    "parameters": dict,
    "risk": str,
    "permission": str,
    "scopes": list,
}
VALID_RISK = {"read", "send", "admin"}
VALID_PERMISSION = {"member", "group_admin", "group_owner", "owner"}
VALID_SCOPE = {"group", "private", "*"}


@pytest.fixture(scope="module")
def catalog() -> list[dict]:
    """JSON 里的原始条目（**不含**派生字段 sensitivity）。"""
    return json.loads(MANIFEST_JSON.read_text(encoding="utf-8"))


def test_manifest_json_exists_and_is_a_list(catalog):
    assert MANIFEST_JSON.is_file(), f"清单数据文件缺失：{MANIFEST_JSON}"
    assert isinstance(catalog, list)
    assert len(catalog) >= 100, f"清单条目只有 {len(catalog)} 条，疑似被截断"


def test_every_entry_has_required_fields_with_expected_types(catalog):
    problems: list[str] = []
    for i, entry in enumerate(catalog):
        label = entry.get("name") or f"#{i}"
        for field, expected in REQUIRED_FIELDS.items():
            if field not in entry:
                problems.append(f"{label}: 缺字段 {field}")
            elif not isinstance(entry[field], expected):
                problems.append(
                    f"{label}: 字段 {field} 类型应为 {expected.__name__}，实际 {type(entry[field]).__name__}"
                )
    assert not problems, "清单条目结构有误：\n" + "\n".join(problems[:30])


def test_tool_names_are_unique(catalog):
    seen: dict[str, int] = {}
    for entry in catalog:
        seen[entry["name"]] = seen.get(entry["name"], 0) + 1
    dupes = {name: n for name, n in seen.items() if n > 1}
    assert not dupes, f"工具名重复：{dupes}"


def test_enums_are_within_known_values(catalog):
    bad_risk = [e["name"] for e in catalog if e["risk"] not in VALID_RISK]
    bad_perm = [e["name"] for e in catalog if e["permission"] not in VALID_PERMISSION]
    bad_scope = [
        e["name"]
        for e in catalog
        if not e["scopes"] or any(s not in VALID_SCOPE for s in e["scopes"])
    ]
    assert not bad_risk, f"未知 risk 值：{bad_risk}"
    assert not bad_perm, f"未知 permission 值：{bad_perm}"
    assert not bad_scope, f"scopes 为空或含未知值：{bad_scope}"


def test_parameters_look_like_a_json_schema_object(catalog):
    wrong = [e["name"] for e in catalog if e["parameters"].get("type") != "object"]
    assert not wrong, f"parameters 必须是以 type=object 开头的 JSON Schema：{wrong}"
    # properties 若存在必须是 dict（前端/工具调用方直接遍历它）
    bad = [
        e["name"]
        for e in catalog
        if "properties" in e["parameters"] and not isinstance(e["parameters"]["properties"], dict)
    ]
    assert not bad, f"parameters.properties 必须是对象：{bad}"


def test_sensitivity_is_not_baked_into_the_data_file(catalog):
    """策略字段必须由代码派生，不能固化进 JSON。

    固化会让「哪些操作算高危」散落成 175 条里的隐式约定，加一条高危工具时
    无处可审。
    """
    baked = [e.get("name") for e in catalog if "sensitivity" in e]
    assert not baked, f"这些条目把派生字段 sensitivity 写进了数据文件：{baked[:10]}"


def test_loader_derives_sensitivity_for_every_entry(catalog):
    derived = {e["name"]: e["sensitivity"] for e in manifest_module.ONEBOT_TOOLS}
    assert set(derived) == {e["name"] for e in catalog}
    assert all(v in {"normal", "high", "critical"} for v in derived.values())
    # 派生的结果必须与策略名单一致
    for name, level in derived.items():
        if name in manifest_module._CRITICAL_TOOLS:
            assert level == "critical", f"{name} 在 _CRITICAL_TOOLS 里但 sensitivity={level}"
        elif name in manifest_module._HIGH_TOOLS:
            assert level == "high", f"{name} 在 _HIGH_TOOLS 里但 sensitivity={level}"
        else:
            assert level == "normal", f"{name} 不在策略名单里但 sensitivity={level}"


def test_policy_sets_have_no_dangling_names(catalog):
    """策略名单里提到的工具必须真实存在，否则拼错一个字母就静默失去保护。"""
    names = {e["name"] for e in catalog}
    dangling = sorted((manifest_module._CRITICAL_TOOLS | manifest_module._HIGH_TOOLS) - names)
    assert not dangling, f"策略名单引用了清单里不存在的工具：{dangling}"


def test_manifest_module_stays_a_thin_loader():
    """`manifest.py` 必须是加载器，不是数据。

    它原本是 4,792 行字面量。这条测试防止有人把清单再内联回去：
    加载器 + 策略名单 + 文档字符串应当远小于 250 行。
    """
    lines = MANIFEST_PY.read_text(encoding="utf-8").count("\n")
    assert lines < 250, (
        f"manifest.py 有 {lines} 行——清单数据似乎又被内联回 Python 了。"
        f"数据请放 manifest.json，本文件只做加载与 sensitivity 派生。"
    )


def test_json_ships_with_the_package():
    """数据文件必须被声明为 package-data，否则 `pip install` 后加载即崩。"""
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "[tool.setuptools.package-data]" in pyproject, "缺少 package-data 配置"
    assert "app.agent.tools.onebot" in pyproject and "*.json" in pyproject, (
        "package-data 必须声明 app.agent.tools.onebot = [\"*.json\"]，否则打包后 manifest.json 不会被分发"
    )
