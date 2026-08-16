"""定时任务功能测试：时间解析 / 调度服务。"""

import asyncio
import time
from datetime import datetime, timedelta

from app.llm.scheduler import TaskScheduler
from app.llm.time_parser import advance_repeat, parse_schedule


def _now():
    return datetime(2026, 8, 13, 18, 0, 0)  # 任意固定时刻


# ==================== time_parser ====================

def test_parse_absolute_time():
    r = parse_schedule("明天早上8点", _now())
    assert r["repeat"] == "once"
    assert r["next_at"] == datetime(2026, 8, 14, 8, 0)


def test_parse_today_evening():
    r = parse_schedule("今晚10点", _now())
    assert r["next_at"] == datetime(2026, 8, 13, 22, 0)


def test_parse_passed_bare_time_rolls_next_day():
    r = parse_schedule("15:30", _now())
    assert r["next_at"] == datetime(2026, 8, 14, 15, 30)


def test_parse_future_bare_time_today():
    r = parse_schedule("20:30", _now())
    assert r["next_at"] == datetime(2026, 8, 13, 20, 30)


def test_parse_daily():
    r = parse_schedule("每天早上8点", _now())
    assert r["repeat"] == "daily"
    assert r["next_at"] == datetime(2026, 8, 14, 8, 0)


def test_parse_daily_future_today():
    r = parse_schedule("每天20:00", _now())
    assert r["repeat"] == "daily"
    assert r["next_at"] == datetime(2026, 8, 13, 20, 0)


def test_parse_weekly():
    now = _now()
    r = parse_schedule("每周五下午6点", now)
    assert r["repeat"] == "weekly"
    assert r["weekday"] == 4
    assert r["next_at"].weekday() == 4
    assert r["next_at"].hour == 18
    assert now < r["next_at"] <= now + timedelta(days=7)


def test_parse_bare_weekday_once():
    now = _now()
    r = parse_schedule("周五晚上8点", now)
    assert r["repeat"] == "once"
    assert r["next_at"].weekday() == 4
    assert r["next_at"].hour == 20


def test_parse_next_week():
    now = _now()
    r = parse_schedule("下周一早上9点", now)
    assert r["repeat"] == "once"
    assert r["next_at"].weekday() == 0
    assert r["next_at"].hour == 9
    assert 7 <= (r["next_at"] - now).days <= 13


def test_parse_monthly():
    now = _now()
    r = parse_schedule("每月1号上午9点", now)
    assert r["repeat"] == "monthly"
    assert r["next_at"].day == 1
    assert r["next_at"].hour == 9


def test_parse_relative_minutes():
    now = _now()
    r = parse_schedule("5分钟后", now)
    assert r["repeat"] == "once"
    assert r["next_at"] == now + timedelta(minutes=5)


def test_parse_relative_half_hour():
    now = _now()
    r = parse_schedule("半小时后", now)
    assert r["next_at"] == now + timedelta(minutes=30)


def test_parse_relative_chinese_numeral():
    now = _now()
    r = parse_schedule("十分钟后", now)
    assert r["next_at"] == now + timedelta(minutes=10)


def test_parse_interval():
    now = _now()
    r = parse_schedule("每30分钟", now)
    assert r["repeat"] == "interval"
    assert r["interval_seconds"] == 1800
    assert r["next_at"] == now + timedelta(minutes=30)


def test_parse_interval_hours():
    r = parse_schedule("每2小时", _now())
    assert r["repeat"] == "interval"
    assert r["interval_seconds"] == 7200


def test_parse_am_pm_adjust():
    now = _now()
    assert parse_schedule("晚上8点", now)["next_at"] == datetime(2026, 8, 13, 20, 0)
    assert parse_schedule("下午3点", now)["next_at"] == datetime(2026, 8, 14, 15, 0)
    assert parse_schedule("凌晨2点", now)["next_at"] == datetime(2026, 8, 14, 2, 0)
    assert parse_schedule("晚上12点", now)["next_at"] == datetime(2026, 8, 14, 0, 0)


def test_parse_garbage_returns_none():
    assert parse_schedule("随便什么", _now()) is None
    assert parse_schedule("", _now()) is None


def test_advance_repeat():
    now = _now()
    assert advance_repeat(now, repeat="daily") == now + timedelta(days=1)
    assert advance_repeat(now, repeat="interval", interval_seconds=1800) == now + timedelta(minutes=30)
    assert advance_repeat(datetime(2026, 8, 13, 9, 0), repeat="monthly", dom=13) == datetime(2026, 9, 13, 9, 0)


# ==================== TaskScheduler ====================

class _FakeBot:
    def __init__(self):
        self.sent = []

    async def send_group_msg(self, group_id, message):
        self.sent.append(("g", group_id, message))

    async def send_private_msg(self, user_id, message):
        self.sent.append(("p", user_id, message))


class _FakeConfig:
    def __init__(self, cfg):
        self._cfg = cfg

    def get(self, key, default=None):
        return self._cfg.get(key, default)

    @property
    def raw_config(self):
        return self._cfg


class _FakeServices:
    def __init__(self):
        from app.core.task_manager import get_task_manager

        self.task_manager = get_task_manager()


class _FakeCtx:
    def __init__(self):
        self.bot = _FakeBot()
        self.services = _FakeServices()


class _FakeModule:
    def __init__(self):
        self.bot_id = 99
        self.module_name = "llm_chat_v2"
        self.ctx = _FakeCtx()
        self.config = _FakeConfig({"schedule_enable": True})


async def test_scheduler_schedule_and_trigger(tmp_path):
    module = _FakeModule()
    sched = TaskScheduler(module, data_dir=str(tmp_path))
    try:
        entry = await sched.schedule("private_100", {"trigger": "明天早上8点", "content": "该起床啦"})
        assert entry is not None
        assert entry.repeat == "once"
        assert entry.session_id == "private_100"
        assert entry.is_group is False

        ok = await sched.trigger_now(entry.id)
        assert ok
        bot = module.ctx.bot
        assert bot.sent == [("p", 100, "该起床啦")]
        assert sched.status() == []  # 一次性任务触发后结束
    finally:
        sched.stop()


async def test_scheduler_fire_uses_llm_reply(tmp_path, monkeypatch):
    """触发时应做一次带系统提示词的 LLM 请求：回复来自 LLM、状态标签被剥离。"""
    from app.llm import scheduler as ts_mod

    module = _FakeModule()
    sched = ts_mod.TaskScheduler(module, data_dir=str(tmp_path))

    class _FakeResp:
        ok = True
        text = "<type=posture>站着</type>\n明天早上记得带伞哦~"

    class _FakeProvider:
        async def chat(self, messages, **kw):
            joined = " ".join(m["content"] for m in messages)
            assert "该起床啦" in joined          # 任务内容进了请求
            assert messages[0]["role"] == "system"  # 系统提示词在
            return _FakeResp()

    monkeypatch.setattr(ts_mod, "get_provider", lambda cfg: _FakeProvider())

    try:
        entry = await sched.schedule("private_100", {"trigger": "明天早上8点", "content": "该起床啦"})
        await sched.trigger_now(entry.id)
        bot = module.ctx.bot
        assert bot.sent == [("p", 100, "明天早上记得带伞哦~")]  # LLM 回复，状态标签已剥离
    finally:
        sched.stop()


async def test_scheduler_repeat_advance(tmp_path):
    module = _FakeModule()
    sched = TaskScheduler(module, data_dir=str(tmp_path))
    try:
        entry = await sched.schedule("group_500", {"trigger": "每天20:00", "content": "打卡"})
        assert entry.repeat == "daily"
        assert entry.is_group is True

        ok = await sched.trigger_now(entry.id)
        assert ok
        bot = module.ctx.bot
        assert any(s[0] == "g" and s[1] == 500 for s in bot.sent)
        rows = sched.status()
        assert len(rows) == 1
        assert rows[0]["next_trigger_time"] > time.time()  # 周期任务推进到未来
    finally:
        sched.stop()


async def test_scheduler_cancel(tmp_path):
    module = _FakeModule()
    sched = TaskScheduler(module, data_dir=str(tmp_path))
    try:
        entry = await sched.schedule("private_100", {"trigger": "明天早上8点", "content": "提醒"})
        assert sched.cancel(entry.id)
        assert sched.status() == []
        assert sched.cancel(entry.id) is False
    finally:
        sched.stop()


async def test_scheduler_session_type_derived_from_id(tmp_path):
    """回归：由 session_id 推导私聊/群聊，杜绝 is_group/is_private 传反导致任务发错目标。"""
    module = _FakeModule()
    sched = TaskScheduler(module, data_dir=str(tmp_path))
    try:
        priv = await sched.schedule("private_100", {"trigger": "明天早上8点", "content": "私聊提醒"})
        grp = await sched.schedule("group_500", {"trigger": "明天早上9点", "content": "群聊提醒"})
        assert priv.is_group is False and priv.target == "100"
        assert grp.is_group is True and grp.target == "500"

        await sched.trigger_now(priv.id)
        await sched.trigger_now(grp.id)
        bot = module.ctx.bot
        assert any(s[0] == "p" and s[1] == 100 for s in bot.sent)
        assert any(s[0] == "g" and s[1] == 500 for s in bot.sent)
    finally:
        sched.stop()


async def test_scheduler_persistence(tmp_path):
    module = _FakeModule()
    sched = TaskScheduler(module, data_dir=str(tmp_path))
    try:
        await sched.schedule("private_100", {"trigger": "明天早上8点", "content": "持久化提醒"})
    finally:
        sched.stop()

    # 重启后恢复
    sched2 = TaskScheduler(module, data_dir=str(tmp_path))
    try:
        rows = sched2.status()
        assert len(rows) == 1
        assert rows[0]["content"] == "持久化提醒"
    finally:
        sched2.stop()


async def test_scheduler_heals_wrong_is_group(tmp_path):
    """回归：历史版本可能把私聊任务存成 is_group=True，加载时按 session_id 校正。"""
    import json
    from datetime import datetime, timedelta

    from app.llm.scheduler import TaskEntry

    module = _FakeModule()
    data_file = str(tmp_path) + f"/tasks_data_{module.bot_id}.json"
    bad = TaskEntry(
        task_id="abcd1234", session_id="private_100", is_group=True,  # 故意存反
        target="100", trigger_expr="明天早上8点", content="提醒",
        next_at=datetime.now() + timedelta(days=1),
    )
    with open(data_file, "w", encoding="utf-8") as f:
        json.dump({"tasks": [bad.to_dict()]}, f, ensure_ascii=False)

    sched = TaskScheduler(module, data_dir=str(tmp_path))
    try:
        assert "abcd1234" in sched._tasks
        e = sched._tasks["abcd1234"]
        assert e.is_group is False, "私聊任务加载后应校正为私聊"
        assert e.target == "100"
    finally:
        sched.stop()


async def test_scheduler_restore_removes_expired_once(tmp_path):
    """启动恢复：过期的一次性任务被移除，周期任务推进到未来并重新武装。"""
    import json
    from datetime import datetime, timedelta

    from app.llm.scheduler import TaskEntry

    module = _FakeModule()
    data_file = str(tmp_path) + f"/tasks_data_{module.bot_id}.json"
    past = datetime.now() - timedelta(hours=2)
    entries = [
        TaskEntry(task_id="expired01", session_id="private_1", is_group=False, target="1",
                  trigger_expr="x", content="过期", repeat="once", next_at=past),
        TaskEntry(task_id="daily01", session_id="group_2", is_group=True, target="2",
                  trigger_expr="每天8点", content="每日", repeat="daily", next_at=past),
    ]
    with open(data_file, "w", encoding="utf-8") as f:
        json.dump({"tasks": [e.to_dict() for e in entries]}, f, ensure_ascii=False)

    sched = TaskScheduler(module, data_dir=str(tmp_path))
    try:
        assert "expired01" not in sched._tasks, "过期一次性任务应被移除"
        assert "daily01" in sched._tasks, "周期任务应保留"
        assert sched._tasks["daily01"].next_at > datetime.now(), "周期任务应推进到未来"
        assert "daily01" in sched._timers, "周期任务应重新武装计时器"
    finally:
        sched.stop()


async def test_scheduler_bad_trigger(tmp_path):
    module = _FakeModule()
    sched = TaskScheduler(module, data_dir=str(tmp_path))
    try:
        entry = await sched.schedule("private_100", {"trigger": "随便什么", "content": "提醒"})
        assert entry is None
    finally:
        sched.stop()


async def test_scheduler_disabled(tmp_path):
    module = _FakeModule()
    module.config._cfg["schedule_enable"] = False
    sched = TaskScheduler(module, data_dir=str(tmp_path))
    try:
        entry = await sched.schedule("private_100", {"trigger": "明天早上8点", "content": "提醒"})
        assert entry is None
    finally:
        sched.stop()


# ==================== schedule_task 工具（原生 function calling） ====================

async def test_schedule_tool_handler(tmp_path):
    """schedule_task 工具：create / list / delete / 归属过滤 / 错误时间。"""
    from app.llm.scheduler import handle_schedule_tool

    module = _FakeModule()
    sched = TaskScheduler(module, data_dir=str(tmp_path))
    module.scheduler = sched  # 真实 on_load 会挂载
    try:
        r = await handle_schedule_tool(module, "private_100", True,
                                       {"action": "create", "trigger": "明天早上8点", "note": "该吃药了"})
        assert r.startswith("success: 已创建定时任务"), r
        assert "该吃药了" in r or "id=" in r

        # list 本会话
        r = await handle_schedule_tool(module, "private_100", True, {"action": "list"})
        assert "本会话定时任务" in r
        assert "该吃药了" in r

        # 别的会话 list 为空
        r = await handle_schedule_tool(module, "group_9", False, {"action": "list"})
        assert "没有定时任务" in r

        # 无法解析的时间 → 明确错误（LLM 可据此自纠错）
        r = await handle_schedule_tool(module, "private_100", True,
                                       {"action": "create", "trigger": "随便什么", "note": "x"})
        assert r.startswith("error: 无法解析时间表达式"), r

        # delete 不存在的任务
        r = await handle_schedule_tool(module, "private_100", True, {"action": "delete", "job_id": "deadbeef"})
        assert r.startswith("error:")

        # delete 自己的任务
        tid = sched.status()[0]["task_id"]
        r = await handle_schedule_tool(module, "private_100", True, {"action": "delete", "job_id": tid})
        assert r.startswith("success: 已删除"), r
        assert sched.status() == []

        # 非法 action
        r = await handle_schedule_tool(module, "private_100", True, {"action": "boom"})
        assert r.startswith("error:")
    finally:
        sched.stop()


def test_schedule_tool_openai_definition():
    """工具定义可转成 OpenAI 原生 function 格式。"""
    from app.llm.scheduler import SCHEDULE_TASK_SCHEMA
    from app.llm.tool import ToolSpec

    spec = ToolSpec(name="schedule_task", description="管理定时任务",
                    parameters=SCHEDULE_TASK_SCHEMA, handler=lambda args: "ok")
    oai = spec.to_openai()
    assert oai["type"] == "function"
    assert oai["function"]["name"] == "schedule_task"
    assert oai["function"]["parameters"]["required"] == ["action"]


# ==================== 兜底：定时意图检测 ====================

def test_has_schedule_intent():
    from app.llm.scheduler import has_schedule_intent

    assert has_schedule_intent("能在今天晚上11点提醒我睡觉吗") is True
    assert has_schedule_intent("明天早上8点记得提醒我吃药") is True
    assert has_schedule_intent("5分钟后叫我") is True
    assert has_schedule_intent("每天中午12点提醒我喝水") is True
    # 无提醒动词 → 不排
    assert has_schedule_intent("我们明天下午开会") is False
    assert has_schedule_intent("今天天气怎么样") is False
    # 有动词但无具体时刻 → 不排
    assert has_schedule_intent("提醒我明天开会") is False


def test_extract_reminder_note():
    from app.llm.scheduler import extract_reminder_note

    assert "睡觉" in extract_reminder_note("能在今天晚上11点提醒我睡觉吗")
    assert "吃药" in extract_reminder_note("明天早上8点记得提醒我吃药")
    assert "喝水" in extract_reminder_note("每天中午12点提醒我喝水")
    assert extract_reminder_note("5分钟后叫我")  # 非空即可
