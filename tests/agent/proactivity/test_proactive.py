"""主动消息测试：启用判断 / 免打扰 / 私聊调度 / 停止 / 启动恢复。"""

import time
from datetime import datetime

import pytest

from app.agent.proactivity.manager import ProactiveManager
from app.core.task_manager import TaskManager
from app.plugin.base import ModuleConfig

BASE = {
    "proactive_friend_enable": False,
    "proactive_group_enable": False,
    "proactive_friend_sessions": [],
    "proactive_group_sessions": [],
    "proactive_min_interval_minutes": 30,
    "proactive_max_interval_minutes": 60,
    "proactive_max_unanswered": 3,
    "proactive_quiet_hours_start": 1,
    "proactive_quiet_hours_end": 7,
    "proactive_group_idle_minutes": 10,
    "proactive_prompt": "test {{unanswered_count}} {{current_time}}",
    "system_prompt": "sys",
    "model": "m",
    "history_rounds": 5,
    "max_tokens": 100,
    "temperature": 0.7,
}


class _Svc:
    def __init__(self, data):
        self.data = data

    def get_module_config(self, module, bot_id):
        return self.data

    def set_module_config(self, module, bot_id, data, persist=True):
        self.data = data


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_group_msg(self, group_id, message):
        self.sent.append(("g", group_id, message))

    async def send_private_msg(self, user_id, message):
        self.sent.append(("p", user_id, message))


def make_pm(tmp_path, config_overrides=None, _bid="bot_act"):
    data = {**BASE, **(config_overrides or {})}
    svc = _Svc(data)
    _config = ModuleConfig("llm_chat_v2", _bid, dict(BASE), svc)

    class Ctx:
        bot = FakeBot()
        services = type("S", (), {"task_manager": TaskManager()})()

    class Mod:
        bot_id = _bid
        module_name = "llm_chat_v2"
        ctx = Ctx()
        config = _config

    pm = ProactiveManager(Mod())
    pm._file = str(tmp_path / "proactive_data.json")
    return pm, Mod()


def test_session_enabled(tmp_path):
    pm, _ = make_pm(tmp_path, {"proactive_friend_enable": True, "proactive_friend_sessions": ["10001"]})
    assert pm._session_enabled("private_10001", is_group=False) is True
    assert pm._session_enabled("private_99999", is_group=False) is False
    assert pm._session_enabled("group_10001", is_group=True) is False  # 群列表未配置
    pm2, _ = make_pm(tmp_path,
                     {"proactive_group_enable": True, "proactive_group_sessions": ["20002"]})
    assert pm2._session_enabled("group_20002", is_group=True) is True


def test_quiet_time_range(tmp_path):
    pm, _ = make_pm(tmp_path, {})
    # start==end → 永不免打扰
    pm.module.config.set("proactive_quiet_hours_start", 0)
    pm.module.config.set("proactive_quiet_hours_end", 0)
    assert pm._is_quiet_time() is False
    # 覆盖当前小时 → 免打扰
    now_h = datetime.now().hour
    pm.module.config.set("proactive_quiet_hours_start", now_h)
    pm.module.config.set("proactive_quiet_hours_end", now_h + 1)
    assert pm._is_quiet_time() is True


async def test_private_schedules_on_message(tmp_path):
    pm, _ = make_pm(tmp_path, {"proactive_friend_enable": True, "proactive_friend_sessions": ["10001"]})
    await pm.on_message("private_10001", is_group=False, is_self=False)
    assert "private_10001" in pm._timers
    assert pm._data["private_10001"]["unanswered_count"] == 0
    # 自身消息不重置/不调度
    await pm.on_message("private_10001", is_group=False, is_self=True)
    assert "private_10001" in pm._timers  # 已调度的保留


async def test_group_resets_silence(tmp_path):
    pm, _ = make_pm(tmp_path, {"proactive_group_enable": True, "proactive_group_sessions": ["20002"]})
    await pm.on_message("group_20002", is_group=True, is_self=False)
    assert "group_20002" in pm._group_timers
    await pm.on_bot_sent("group_20002", is_group=True)
    assert "group_20002" in pm._group_timers  # 重新计时


async def test_disabled_session_no_timer(tmp_path):
    pm, _ = make_pm(tmp_path, {"proactive_friend_enable": True, "proactive_friend_sessions": []})
    await pm.on_message("private_10001", is_group=False, is_self=False)
    assert "private_10001" not in pm._timers


async def test_stop_cancels_timers(tmp_path):
    pm, _ = make_pm(tmp_path, {"proactive_friend_enable": True, "proactive_friend_sessions": ["10001"]})
    await pm.on_message("private_10001", is_group=False, is_self=False)
    assert "private_10001" in pm._timers
    pm.stop()
    assert not pm._timers


async def test_restore_reschedules_expired(tmp_path):
    """启动恢复：私聊会话的触发时间已过期 → 重新随机设置一次。"""
    pm, _ = make_pm(tmp_path, {"proactive_friend_enable": True, "proactive_friend_sessions": ["10001"]})
    pm._data["private_10001"] = {"next_trigger_time": time.time() - 600}  # 已过期
    pm._restore()
    try:
        assert "private_10001" in pm._timers, "应重新武装计时器"
        assert pm._data["private_10001"]["next_trigger_time"] > time.time(), "应重新设置到未来"
    finally:
        pm.stop()


async def test_restore_keeps_future_timer(tmp_path):
    """启动恢复：触发时间未过期 → 按原时间武装，不重设。"""
    pm, _ = make_pm(tmp_path, {"proactive_friend_enable": True, "proactive_friend_sessions": ["10001"]})
    future = time.time() + 300
    pm._data["private_10001"] = {"next_trigger_time": future}
    pm._restore()
    try:
        assert "private_10001" in pm._timers
        assert abs(pm._data["private_10001"]["next_trigger_time"] - future) < 1, "原触发时间应保留"
    finally:
        pm.stop()


async def test_restore_skips_disabled(tmp_path):
    """启动恢复：未启用的会话不武装计时器。"""
    pm, _ = make_pm(tmp_path, {"proactive_friend_enable": True, "proactive_friend_sessions": []})
    pm._data["private_99999"] = {"next_trigger_time": time.time() - 600}
    pm._restore()
    try:
        assert "private_99999" not in pm._timers
        assert "private_99999" not in pm._group_timers
    finally:
        pm.stop()


# ==================== 主动消息真正发起（回归：取值阶段曾直接崩溃） ====================


class _Boom(Exception):
    """哨兵：证明执行已经走到 provider 调用，而不是在取值阶段就炸了。"""


async def test_check_and_chat_reaches_provider_call(tmp_path, monkeypatch):
    """回归：_check_and_chat 里 ``_max_tool_rounds`` 曾排在其函数内 import 之前被调用。

    Python 把函数内 import 视为**局部绑定**，因此那个调用会抛 UnboundLocalError，
    表现为「主动消息永远发不出去」——#llm proactive 与所有到点的主动发言一起失效。
    本用例在修复前必红。
    """
    pm, _ = make_pm(tmp_path, {
        "proactive_friend_enable": True,
        "proactive_friend_sessions": ["10001"],
        # 关掉免打扰窗口：否则用例在凌晨 1-7 点会提前返回，覆盖不到目标分支
        "proactive_quiet_hours_start": 0,
        "proactive_quiet_hours_end": 0,
    })
    # 生产里 module.config 是 AgentConfig（真有 set_session）；测试替身用 ModuleConfig，
    # 其 __getattr__ 会把缺失属性返回 None，导致 hasattr(config, "set_session") 恒为 True
    # 却在调用时 TypeError —— 这里补上真实方法，让用例能走到待验证的那一行。
    pm.module.config.set_session = lambda session_id: None
    pm.module.config.clear_session = lambda: None

    async def fake_tools(*args, **kwargs):
        return [], [], None, None

    async def fake_materials(**kwargs):
        return {"history": [], "memory_text": ""}

    async def fake_focus(*args, **kwargs):
        return ""

    def fake_group_log(*args, **kwargs):
        return ""

    async def fake_chat(*args, **kwargs):
        raise _Boom("已到达 provider 调用")

    monkeypatch.setattr("app.agent.loop.chat.build_initiative_tools", fake_tools)
    monkeypatch.setattr("app.agent.assembly.inputs.collect_history_materials", fake_materials)
    monkeypatch.setattr("app.agent.context.referent.focus_only_block", fake_focus)
    monkeypatch.setattr("app.agent.group_log.context.build_context_text", fake_group_log)
    # 请求出口已收口到 runtime.invoker（默认实现按属性查找转调 providers）
    monkeypatch.setattr("app.agent.providers.chat_with_fallback", fake_chat)

    with pytest.raises(_Boom):
        await pm.manual_trigger("private_10001")
