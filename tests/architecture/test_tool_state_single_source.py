"""架构闸门：工具状态必须只有一个判定点、一个存储、一条注入路径。

## 锁死的三件事

1. **写读同源**：模块拥有的键不能写进 agent 配置（`POST /api/agent/config` 必须拒）
2. **没有 VIP 通道**：注入过滤里不许出现"某来源直接放行"
3. **显示即真相**：界面拿到的 `available` 必须等于解析器结论

历史 bug：`onebot_tools_enable` 界面写 agent 配置、策略读模块配置 →
关掉后工具照用；模块卸载后又回退到 agent 旧值复活。
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]


# ==================== ① 写读同源 ====================


def test_agent_config_api_rejects_module_owned_keys():
    """`POST /api/agent/config` 必须调用模块键校验。"""
    src = (ROOT / "app/webui/api/agent.py").read_text(encoding="utf-8")
    assert "module_owned_keys" in src, "agent 配置接口没有做模块键拦截"
    assert "已归属模块" in src, "拦截时应给出可读的 400 提示"


def test_module_owned_keys_are_disjoint_from_core_keys():
    from app.agent.config_partition import CORE_KEYS, MODULE_KEYS

    owned: set[str] = set()
    for keys in MODULE_KEYS.values():
        owned.update(keys)
    overlap = owned & set(CORE_KEYS)
    assert not overlap, f"同一配置键被两方同时拥有：{sorted(overlap)}"


def test_onebot_switches_belong_to_the_tools_module():
    """OneBot 工具开关必须归模块，agent 配置不该是它们的家。"""
    from app.agent.config_partition import CORE_KEYS, MODULE_KEYS

    for key in ("onebot_tools_enable", "onebot_tools_allowed", "onebot_tools_denied"):
        assert key in MODULE_KEYS.get("llm_tools_extra", ()), f"{key} 应归 llm_tools_extra"
        assert key not in CORE_KEYS, f"{key} 不该出现在本体配置"


# ==================== ② 没有 VIP 通道 ====================


def test_tool_injection_has_no_source_specific_bypass():
    """注入过滤必须统一走解析器，不能按来源放行。"""
    src = (ROOT / "app/agent/loop/tool_source.py").read_text(encoding="utf-8")
    assert "resolve_tool_state" in src, "注入路径没有使用统一判定"
    tree = ast.parse(src)
    for node in ast.walk(tree):
        # 禁止 `if spec.source == "xxx": filtered.append(spec)` 这类捷径
        if isinstance(node, ast.If):
            test = ast.unparse(node.test)
            if "source" in test:
                body = ast.unparse(ast.Module(body=node.body, type_ignores=[]))
                assert "filtered.append" not in body, (
                    f"发现按来源直接放行的分支：if {test}"
                )


# ==================== ③ 显示即真相 ====================


def _base_runtime(**overrides):
    """一个"模块都装着、开关都开着"的运行时。"""
    config = {
        "tools_disabled": [],
        "module_tools_enabled": {},
        "system_tools_enabled": {},
        "mcp_tools_enabled": {},
        "onebot_tools_enable": True,
    }
    cfg = SimpleNamespace(get=lambda k, d=None: config.get(k, d))
    runtime = SimpleNamespace(
        bot_id="1",
        config=cfg,
        tools_options=SimpleNamespace(get=lambda k, d=None: config.get(k, d)),
        llm_tools=None,
        mcp_service=None,
    )
    for key, value in overrides.items():
        setattr(runtime, key, value)
    return runtime


def test_resolver_reports_module_disabled_instead_of_resurrecting():
    """模块卸载（tools_options=None）时，旧的 agent 配置不能让工具复活。"""
    from app.agent.tools.state import REASON_MODULE_DISABLED, resolve_tool_state

    rt = _base_runtime(tools_options=None)
    state = resolve_tool_state(rt, name="send_poke", source="onebot")
    assert not state.available
    assert state.reason == REASON_MODULE_DISABLED, state


def test_resolver_respects_user_master_list():
    """统一总表 `tools_disabled` 对**所有来源**都生效。"""
    from app.agent.tools.state import REASON_USER_DISABLED, resolve_tool_state

    rt = _base_runtime()
    rt.config = SimpleNamespace(
        get=lambda k, d=None: {"tools_disabled": ["send_poke"]}.get(k, d) if k == "tools_disabled" else d
    )
    state = resolve_tool_state(rt, name="send_poke", source="onebot")
    assert not state.available
    assert state.reason == REASON_USER_DISABLED, state


def test_resolver_allows_when_everything_is_on():
    from app.agent.tools.state import REASON_OK, resolve_tool_state

    state = resolve_tool_state(_base_runtime(), name="send_poke", source="onebot")
    assert state.available, state
    assert state.reason == REASON_OK


def test_every_state_has_a_human_reason():
    """不可用时必须给出人能看懂的原因（页面直接显示它）。"""
    from app.agent.tools.state import resolve_all_tool_states

    for state in resolve_all_tool_states(_base_runtime()):
        if not state.available:
            assert state.reason_text, f"{state.name} 没有原因说明"
            assert state.reason_text != state.reason, f"{state.name} 只有机器原因码"


def test_set_tool_disabled_writes_one_place():
    """per-tool 开关只写 `tools_disabled`，不再散写三套 map。"""
    from app.agent.tools.state import USER_TOOLS_DISABLED_KEY, set_tool_disabled

    written: dict = {}

    class _Config:
        def get(self, key, default=None):
            return written.get(key, default)

        def set(self, key, value, auto_save=False):
            written[key] = value

        def save(self):
            written["_saved"] = True

    rt = SimpleNamespace(config=_Config())
    set_tool_disabled(rt, "send_poke", disabled=True)
    assert written[USER_TOOLS_DISABLED_KEY] == ["send_poke"]
    assert written.get("_saved") is True, "改完要落盘"

    set_tool_disabled(rt, "send_poke", disabled=False)
    assert written[USER_TOOLS_DISABLED_KEY] == []
