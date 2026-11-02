"""配置链契约：模块配置必须读得到，不被排在前面的视图吞掉。

回归的是 2026-10-31 实测复现的缺陷：
``ConfigView`` 对非自有键回退 agent 配置，而 ``ChainedConfig`` 取"第一个非 None"，
于是 ``output_options`` 把 ``user_message_format`` 用 agent 旧值答掉，
``llm_assembly`` 里用户真正保存的值永远读不到。
"""

from __future__ import annotations

from app.agent.delivery.output import ConfigView
from app.agent.policy import ChainedConfig


class _Cfg(dict):
    """模拟 AgentConfig / ModuleConfig：只保证有 get()。"""

    def get(self, key, default=None):  # noqa: D102 - 测试替身
        return dict.get(self, key, default)


class _Runtime:
    def __init__(self, base, **views):
        self.config = base
        for name, view in views.items():
            setattr(self, name, view)


BASE = _Cfg(
    user_message_format="legacy",
    participation="medium",
    stream_output=False,
    unrelated="agent-value",
)
OUT_MODULE = _Cfg(mode="stream")
ASM_MODULE = _Cfg(user_message_format="single_line")

OUT_VIEW = ConfigView(BASE, OUT_MODULE, ["mode", "stream_output"])
ASM_VIEW = ConfigView(BASE, ASM_MODULE, ["user_message_format", "participation"])


def _chain(**views) -> ChainedConfig:
    return ChainedConfig(_Runtime(BASE, **views))


def test_config_view_exposes_ownership():
    assert OUT_VIEW.owns("mode") is True
    assert OUT_VIEW.owns("user_message_format") is False
    assert OUT_VIEW.owned_keys == frozenset({"mode", "stream_output"})


def test_later_view_is_not_swallowed_by_earlier_view():
    """核心回归：排在前面的视图不能用自己的 agent 回退值答掉别人的键。"""
    chain = _chain(output_options=OUT_VIEW, assembly_options=ASM_VIEW)
    assert chain.get("user_message_format") == "single_line"
    assert chain.get("participation") == "medium"  # 模块没存 → 回落 agent


def test_owner_wins_and_order_is_priority():
    """链里靠前的视图拥有该键时，后面的视图不能抢。"""
    first = ConfigView(BASE, _Cfg(user_message_format="first"), ["user_message_format"])
    second = ConfigView(BASE, _Cfg(user_message_format="second"), ["user_message_format"])
    assert _chain(context_options=first, memory_options=second).get("user_message_format") == "first"


def test_no_owner_falls_back_to_agent_config():
    chain = _chain(context_options=OUT_VIEW, assembly_options=ASM_VIEW)
    assert chain.get("unrelated") == "agent-value"
    assert chain.get("missing_key", "fallback") == "fallback"


def test_view_without_ownership_info_still_participates():
    """兼容：第三方直接塞进来的普通配置对象没有 owned_keys，仍全量参与。"""
    class _Plain:
        def get(self, key, default=None):
            return {"mode": "blocking"}.get(key, default)

    chain = _chain(context_options=_Plain(), assembly_options=ASM_VIEW)
    assert chain.get("mode") == "blocking"
    assert chain.get("user_message_format") == "single_line"
