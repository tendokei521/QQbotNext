"""「NapCat 工具」改名「OneBot 工具」后的配置兼容回归。

改名本身只是文案清理，**真正的风险在配置键**：老用户库里存的是 ``napcat_tools_*``，
升级后如果只认 ``onebot_tools_*``，读不到就回落默认值——而
``onebot_tools_enable`` 默认 ``False``，等于把用户已经打开的开关静默关掉。
本文件把这个不变量钉死：**旧键仍生效，新键优先，且不改写用户数据**。
"""

from __future__ import annotations

from app.llm.config import (
    AGENT_CONFIG_MODULE,
    DEFAULT_LLM_CONFIG,
    AgentConfig,
    legacy_key_of,
)

LEGACY_PAIRS = [
    ("onebot_tools_enable", "napcat_tools_enable"),
    ("onebot_tools_allowed", "napcat_tools_allowed"),
    ("onebot_tools_denied", "napcat_tools_denied"),
    ("onebot_tools_max_result", "napcat_tools_max_result"),
    ("onebot_tools_debug", "napcat_tools_debug"),
    ("onebot_tools_log_enabled", "napcat_tools_log_enabled"),
    ("onebot_tool_overrides", "napcat_tool_overrides"),
]


class _Svc:
    """最小 ConfigService：只需要按 (module, bot) 存取一份 dict。"""

    def __init__(self, stored: dict | None = None) -> None:
        self.stored = dict(stored or {})
        self.saved: dict | None = None

    def get_module_config(self, module, bot_id):
        return self.stored.get(bot_id, {})

    def set_module_config(self, module, bot_id, data, persist=True):
        self.stored[bot_id] = dict(data)
        self.saved = dict(data)

    def get_module_authority(self, module, bot_id):
        return {}

    def set_module_authority(self, module, bot_id, data, persist=True):
        return None

    def get_config_routes(self):
        return {}

    def get_config_profile(self, profile_id):
        return None


def _config(stored: dict | None = None) -> tuple[AgentConfig, _Svc]:
    svc = _Svc({1: stored or {}})
    return AgentConfig(svc, 1), svc


# ==================== 键映射表 ====================


def test_legacy_key_mapping_covers_every_onebot_key():
    """每一个 onebot_* 配置键都要有旧键映射（否则改名就是丢设置）。"""
    for new_key, old_key in LEGACY_PAIRS:
        assert new_key in DEFAULT_LLM_CONFIG, f"{new_key} 不在默认配置里"
        assert legacy_key_of(new_key) == old_key
    # 不再是工具前缀的键不需要映射
    assert legacy_key_of("group_log_enable") == ""
    assert legacy_key_of("napcat_tools_enable") == ""


# ==================== 读取：旧键生效 ====================


def test_old_keys_still_take_effect_after_rename():
    """用户库里的 napcat_* 值必须照常读到（这是升级不丢设置的核心断言）。"""
    cfg, _ = _config({
        "napcat_tools_enable": True,
        "napcat_tools_allowed": ["send_msg"],
        "napcat_tools_denied": ["set_group_kick"],
        "napcat_tools_max_result": 555,
        "napcat_tools_debug": True,
        "napcat_tools_log_enabled": False,
        "napcat_tool_overrides": {"send_poke": {"permission": "member"}},
    })
    assert cfg.get("onebot_tools_enable") is True
    assert cfg.get("onebot_tools_allowed") == ["send_msg"]
    assert cfg.get("onebot_tools_denied") == ["set_group_kick"]
    assert cfg.get("onebot_tools_max_result") == 555
    assert cfg.get("onebot_tools_debug") is True
    assert cfg.get("onebot_tools_log_enabled") is False
    assert cfg.get("onebot_tool_overrides") == {"send_poke": {"permission": "member"}}


def test_new_key_wins_over_legacy_key():
    """两边都有时以新键为准（新键是唯一事实源）。"""
    cfg, _ = _config({
        "onebot_tools_enable": True,
        "napcat_tools_enable": False,
        "onebot_tools_max_result": 100,
        "napcat_tools_max_result": 200,
    })
    assert cfg.get("onebot_tools_enable") is True
    assert cfg.get("onebot_tools_max_result") == 100


def test_missing_keys_fall_back_to_defaults():
    cfg, _ = _config({})
    assert cfg.get("onebot_tools_enable") is False
    assert cfg.get("onebot_tools_max_result") == DEFAULT_LLM_CONFIG["onebot_tools_max_result"]


def test_reading_legacy_does_not_rewrite_user_data():
    """兼容读取是内存适配：不得把旧键抹掉或替用户改名（避免不可逆的静默迁移）。"""
    cfg, svc = _config({"napcat_tools_enable": True, "napcat_tools_debug": True})
    cfg.get("onebot_tools_enable")
    cfg.get("onebot_tools_debug")
    assert svc.stored[1] == {"napcat_tools_enable": True, "napcat_tools_debug": True}


def test_raw_config_exposes_new_keys_for_legacy_users():
    """raw_config（下发到前端/写库）应按新键口径给出旧用户的值。"""
    cfg, _ = _config({"napcat_tools_enable": True, "napcat_tools_max_result": 777})
    raw = cfg.raw_config
    assert raw["onebot_tools_enable"] is True
    assert raw["onebot_tools_max_result"] == 777
    # 旧键仍原样保留（不丢历史，便于回滚/排查）
    assert raw["napcat_tools_enable"] is True


def test_agent_config_module_name_unchanged():
    """存储命名空间仍是 agent：本改动不涉及配置库位置，避免双重迁移。"""
    assert AGENT_CONFIG_MODULE == "agent"
