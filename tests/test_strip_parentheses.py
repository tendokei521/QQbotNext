"""括号清洗测试：strip/maybe_strip 函数 + 历史写入只清历史、展示保留原文。"""

import asyncio
import types

from app.llm.chat import _clean_output_for_history, generate_response
from app.llm.config import DEFAULT_LLM_CONFIG
from app.llm.memory.manager import MemoryManager
from app.llm.session import SessionManager
from app.llm.tags import maybe_strip_parentheses, strip_parentheses


# ---- strip_parentheses ----
def test_strip_parentheses_basic():
    assert strip_parentheses("好的（笑）我知道了") == "好的我知道了"
    assert strip_parentheses("好的(备注)我知道了") == "好的我知道了"
    assert strip_parentheses("（笑）好的～") == "好的"
    assert strip_parentheses("好的～（这段很简单）") == "好的"


def test_strip_parentheses_keep_unclosed_and_no_nuke():
    # 未闭合括号不剥
    assert strip_parentheses("还在说（没闭合") == "还在说（没闭合"
    # 嵌套：整段都是括号 → 不清空，保留原文（避免触发空回复兜底）
    out = strip_parentheses("（外层(内层)）")
    assert out != ""
    assert "外层" in out
    # 纯括号（单层）同样不清空
    assert strip_parentheses("（微笑）") == "（微笑）"
    # 嵌套但内含正文 → 正常剥
    assert strip_parentheses("（好的（嗯））好的") == "好的"


def test_maybe_strip_parentheses_toggle():
    assert maybe_strip_parentheses("好（哦）", enabled=True) == "好"
    assert maybe_strip_parentheses("好（哦）", enabled=False) == "好（哦）"


# ---- _clean_output_for_history ----
class _Cfg:
    def __init__(self, enabled=True):
        self.enabled = enabled

    def get(self, key, default=None):
        return self.enabled if key == "clean_output_parentheses" else default


def test_clean_output_for_history_respects_switch():
    assert _clean_output_for_history(_Cfg(True), "我记住了（笑）") == "我记住了"
    assert _clean_output_for_history(_Cfg(False), "我记住了（笑）") == "我记住了（笑）"
    assert _clean_output_for_history(None, "我记住了（笑）") == "我记住了"  # 无 config 默认清洗
    assert _clean_output_for_history(_Cfg(True), "") == ""


# ---- 端到端：展示保留原文、历史已清洗 ----
class _Tools:
    def enabled_specs(self):
        return []


class _Skills:
    def prompt_blocks(self):
        return []


def _make(bot_id, data=None, enabled=None):
    rt = types.SimpleNamespace()
    rt.bot_id = bot_id
    rt.config = _FullConfig(data, enabled)
    rt.memory = MemoryManager(rt)
    rt.llm_tools = _Tools()
    rt.skills = _Skills()
    rt.scheduler = None
    rt.proactive = None
    rt.provider_config = lambda: {"api_key": "k"}
    rt.provider_chain = lambda: [{"provider": "openai", "api_key": "k", "model": "m"}]
    return rt


class _FullConfig:
    def __init__(self, data=None, enabled=None):
        self.data = dict(DEFAULT_LLM_CONFIG)
        self.data.update({"memory_enable": True, "experimental_long_term_memory": True})
        if data:
            self.data.update(data)
        if enabled is not None:
            self.data["clean_output_parentheses"] = enabled

    def get(self, key, default=None):
        if key in self.data and self.data[key] is not None:
            return self.data[key]
        return default


async def _run_once(rt, text, monkeypatch):
    captured = []

    async def fake_chat(chain, messages, **_kw):
        captured.append(messages)
        from app.llm.providers.base import LLMResponse

        if any("记忆提取器" in (m.get("content", "") or "") for m in messages):
            return LLMResponse(text="无")
        return LLMResponse(text="好的，我已经记住了（笑）")

    monkeypatch.setattr("app.llm.chat.chat_with_fallback", fake_chat)
    monkeypatch.setattr("app.llm.providers.chat_with_fallback", fake_chat)
    ev = types.SimpleNamespace(message_type="private", user_id="5", group=None, bot=None, self_id="5")
    ctx = types.SimpleNamespace(session_id="", user_text=text, state={})
    out = await generate_response(rt, ev, ctx)
    await asyncio.sleep(0.05)
    return out


async def test_e2e_history_cleaned_but_display_kept(monkeypatch):
    rt = _make("bot_par_on")
    out = await _run_once(rt, "记住我喜欢喝美式", monkeypatch)
    assert "（笑）" in out  # 本次展示保留原文

    sm = SessionManager("bot_par_on")
    s = sm.get_session("private_5")
    assert s is not None
    last = s.data.history[-1]
    assert last["role"] == "assistant"
    assert "(" not in last["content"] and "（" not in last["content"]
    assert "好的" in last["content"]
    rt.memory.stop()


async def test_e2e_option_disabled_keeps_history(monkeypatch):
    rt = _make("bot_par_off", enabled=False)
    out = await _run_once(rt, "记住我喜欢喝美式", monkeypatch)
    assert "（笑）" in out
    sm = SessionManager("bot_par_off")
    s = sm.get_session("private_5")
    assert s is not None
    last = s.data.history[-1]
    assert "（笑）" in last["content"]  # 关闭时历史保留括号
    rt.memory.stop()
