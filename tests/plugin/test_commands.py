"""统一命令系统测试：声明收集 / 解析匹配 / 分发时机 / skip_llm 隐藏参数。"""

from types import SimpleNamespace

from app.domain.events import GroupMessageEvent, MessageSegment
from app.plugin import loader
from app.plugin.base import BaseModule, ModuleAuthority, ModuleConfig, ModuleContext, ServiceAccess
from app.plugin.commands import (
    DEFAULT_PREFIXES,
    GreedyStr,
    best_match,
    command,
    command_group,
    iter_module_commands,
    match_spec,
    normalize_text,
    render_group_help,
    strip_prefix,
)
from app.plugin.dispatcher import ModuleDispatcher
from app.plugin.hooks import module_hook

# ==================== 夹具 ====================


class FakeConfigService:
    def get_webui_config(self):
        return {"single_service": {}, "multi_group": {"groups": {}}}

    def get_module_config(self, module, bot_id=None):
        return {}

    def get_module_authority(self, module, bot_id):
        return {}


class FakeRegistry:
    def __init__(self, modules):
        self._modules = modules

    def loaded(self):
        return self._modules


class _Svc:
    def get_module_config(self, *a):
        return {}

    def get_module_authority(self, *a):
        return {}


def make_module(bot_id="acc0", permission="member", calls=None, subs=("message_group",)):
    calls = calls if calls is not None else []

    class M(BaseModule):
        name = "命令测试"
        sign = "CmdTest"
        description = ""
        subscribe = subs

        @command("log", description="发送日志")
        async def cmd_log(self, event):
            calls.append(("log", list(event.command.args)))

        @command("open", aliases=("开",), description="打开", skip_llm=False)
        async def cmd_open(self, event):
            calls.append(("open", list(event.command.args)))

        @command("boom")
        async def cmd_boom(self, event):
            raise RuntimeError("命令炸了")

        @command_group("memory", description="长期记忆")
        async def cmd_memory(self, event):
            calls.append(("memory", list(event.command.args)))

        @cmd_memory.command("list", description="列出记忆")
        async def cmd_memory_list(self, event):
            calls.append(("memory list", list(event.command.args)))

        @cmd_memory.command("search", description="检索记忆")
        async def cmd_memory_search(self, event):
            calls.append(("memory search", list(event.command.args)))

    M.permission = permission
    s = _Svc()
    ctx = ModuleContext(
        module_name="cmd_test",
        bot_id=bot_id,
        config=ModuleConfig("cmd_test", bot_id, {}, s),
        authority=ModuleAuthority("cmd_test", bot_id, s),
        services=ServiceAccess(),
    )
    instance = M(ctx)
    instance.module_name = "cmd_test"
    loader.bind_commands(instance, M)
    return instance, calls


class RecordingBot:
    """记录发出的消息（命令节点回"用法提示"时用到）。"""

    def __init__(self):
        self.sent: list = []

    async def send_group_msg(self, group_id, message, **kw):
        self.sent.append(message)
        return {"status": "ok", "data": {"message_id": 1}}

    async def send_private_msg(self, user_id, message, **kw):
        self.sent.append(message)
        return {"status": "ok", "data": {"message_id": 1}}


def event(text, *, user_id=100, role="member", self_id=123, bot=None):
    return GroupMessageEvent(
        event_type="message_group",
        message_type="group",
        user_id=user_id,
        self_id=self_id,
        bot_id=self_id,
        account_id="acc0",
        message_id=1,
        time=0,
        bot=bot,
        user=SimpleNamespace(user_id=user_id, role=role),
        group=SimpleNamespace(group_id=1),
        message=[MessageSegment.text(text)],
    )


def dispatch(modules):
    return ModuleDispatcher(
        registry=FakeRegistry(modules),
        config_service=FakeConfigService(),
        gateway=SimpleNamespace(connections={}),
    )


# ==================== 声明与收集 ====================


def test_collect_commands_includes_groups_and_order():
    instance, _calls = make_module()
    names = [spec.name for spec, _handler in instance._commands]

    assert names == ["log", "open", "boom", "memory", "memory list", "memory search"]
    specs = {spec.name: spec for spec, _h in instance._commands}
    assert specs["memory list"].group == "memory"
    assert specs["open"].aliases == ("开",)
    # 隐藏参数默认值
    assert specs["log"].skip_llm is True
    assert specs["open"].skip_llm is False
    # 子命令继承组上的前缀约定
    assert specs["memory list"].prefixes == DEFAULT_PREFIXES


def test_group_subcommand_can_override_skip_llm():
    class M(BaseModule):
        name = "g"
        sign = "G"
        description = ""
        subscribe = ("message_group",)

        @command_group("cfg", skip_llm=True)
        async def cfg(self, event):
            pass

        @cfg.command("show")
        async def cfg_show(self, event):
            pass

        @cfg.command("peek", skip_llm=False)
        async def cfg_peek(self, event):
            pass

    specs = {spec.name: spec for spec, _m in M.collect_commands()}
    assert specs["cfg"].skip_llm is True
    assert specs["cfg show"].skip_llm is True
    assert specs["cfg peek"].skip_llm is False


# ==================== 解析 ====================


def test_normalize_and_strip_prefix():
    assert normalize_text("  /log    a   b ") == "/log a b"
    assert strip_prefix("／log", DEFAULT_PREFIXES) == ("log", "／")
    assert strip_prefix("/log", DEFAULT_PREFIXES) == ("log", "/")
    assert strip_prefix("log", DEFAULT_PREFIXES) is None  # 默认必须带前缀
    assert strip_prefix("log", ("",)) == ("log", "")
    assert strip_prefix("/", DEFAULT_PREFIXES) is None  # 只有前缀不算命令


def test_match_spec_prefix_alias_case_and_args():
    spec = next(s for s, _ in make_module()[0]._commands if s.name == "open")

    assert match_spec("/open", spec).args == []
    assert match_spec("/open 1 2", spec).args == ["1", "2"]
    assert match_spec("／open", spec).matched_name == "open"
    assert match_spec("/OPEN", spec) is not None  # 大小写不敏感
    assert match_spec("/开", spec).matched_name == "开"  # 别名
    assert match_spec("/opened", spec) is None  # 词边界，不能吃掉更长单词
    assert match_spec("open", spec) is None


def test_best_match_prefers_more_specific_command():
    instance, _calls = make_module()
    entries = iter_module_commands([instance])

    assert best_match("/memory list", entries).matched_name == "memory list"
    assert best_match("/memory", entries).matched_name == "memory"
    assert best_match("/nothing", entries) is None


def test_render_group_help_lists_subcommands():
    instance, _calls = make_module()
    text = render_group_help(iter_module_commands([instance]), "memory")

    assert "memory list" in text
    assert "memory search" in text
    assert "memory boom" not in text


# ==================== 分发（含 skip_llm 隐藏参数） ====================


async def test_command_dispatched_and_llm_stopped_by_default():
    module, calls = make_module()
    ev = event("/log")
    await dispatch([module]).dispatch(ev)

    assert calls == [("log", [])]
    assert ev.command.name == "log"
    assert ev.command.module == "cmd_test"
    assert ev._llm_stop is True  # 隐藏参数 skip_llm 默认 True


async def test_command_skip_llm_false_lets_llm_run():
    module, calls = make_module()
    ev = event("/open")
    await dispatch([module]).dispatch(ev)

    assert calls == [("open", [])]
    assert ev._llm_stop is False


async def test_command_args_are_parsed():
    module, calls = make_module()
    ev = event("/memory search 天气  记忆")
    await dispatch([module]).dispatch(ev)

    assert calls == [("memory search", ["天气", "记忆"])]
    assert ev.command.text == "天气 记忆"


async def test_bare_group_name_hits_group_handler():
    module, calls = make_module()
    ev = event("／memory")
    await dispatch([module]).dispatch(ev)

    assert calls == [("memory", [])]


async def test_non_command_text_does_not_touch_llm():
    module, calls = make_module()
    ev = event("今天天气不错")
    await dispatch([module]).dispatch(ev)

    assert calls == []
    assert ev._llm_stop is False
    assert not hasattr(ev, "command")


async def test_command_blocked_by_module_permission():
    module, calls = make_module(permission="owner")
    ev = event("/log", role="member")
    await dispatch([module]).dispatch(ev)

    assert calls == []
    assert ev._llm_stop is False


async def test_command_exception_still_stops_llm():
    module, calls = make_module()
    ev = event("/boom")
    await dispatch([module]).dispatch(ev)

    assert calls == []
    assert ev._llm_stop is True


async def test_module_hook_observer_still_runs_after_command():
    """命令先落定，@module_hook 的观察者（如 group_log）照常收到事件。"""
    seen = []

    class M(BaseModule):
        name = "obs"
        sign = "Obs"
        description = ""
        subscribe = ("message_group",)

        @command("ping")
        async def ping(self, event):
            seen.append("command")

        @module_hook("message_group", order=10)
        async def on_message(self, event):
            seen.append("module_hook")

    s = _Svc()
    ctx = ModuleContext(
        module_name="obs",
        bot_id="acc0",
        config=ModuleConfig("obs", 123, {}, s),
        authority=ModuleAuthority("obs", 123, s),
        services=ServiceAccess(),
    )
    instance = M(ctx)
    instance._module_hooks = [
        {"event_type": "message_group", "order": 10, "handler": instance.on_message}
    ]
    loader.bind_commands(instance, M)

    await dispatch([instance]).dispatch(event("/ping"))

    assert seen == ["command", "module_hook"]


# ==================== 签名绑参 ====================


def bind_module():
    """声明一组带类型注解的命令，返回 (实例, 调用记录)。"""
    calls = []

    class M(BaseModule):
        name = "bind"
        sign = "Bind"
        description = ""
        subscribe = ("message_group",)

        @command("count")
        async def count(self, event, n: int):
            calls.append(("count", n))

        @command("scale")
        async def scale(self, event, n: float, times: int = 2):
            calls.append(("scale", n, times))

        @command("toggle")
        async def toggle(self, event, on: bool):
            calls.append(("toggle", on))

        @command("say")
        async def say(self, event, text: GreedyStr):
            calls.append(("say", text))

        @command("note")
        async def note(self, event, text: str, tag: str = "默认"):
            calls.append(("note", text, tag))

        @command("maybedigit")
        async def maybedigit(self, event, value: int | str | None = None):
            calls.append(("maybedigit", value))

        @command("broken")
        async def broken(self, event, text: GreedyStr, extra: str):
            calls.append(("broken", text, extra))

    s = _Svc()
    ctx = ModuleContext(
        module_name="bind",
        bot_id="acc0",
        config=ModuleConfig("bind", 123, {}, s),
        authority=ModuleAuthority("bind", 123, s),
        services=ServiceAccess(),
    )
    instance = M(ctx)
    loader.bind_commands(instance, M)
    return instance, calls


def test_parse_signature_skips_event_and_marks_greedy():
    instance, _calls = bind_module()
    specs = {spec.name: spec for spec, _h in instance._commands}

    assert [p.name for p in specs["count"].params] == ["n"]
    assert specs["count"].params[0].annotation is int
    assert specs["note"].params[1].has_default is True
    assert specs["note"].params[1].default == "默认"
    assert specs["say"].params[0].greedy is True


async def test_signature_binds_int_float_default_and_bool():
    module, calls = bind_module()

    await dispatch([module]).dispatch(event("/count 42"))
    await dispatch([module]).dispatch(event("/scale 1.5"))
    await dispatch([module]).dispatch(event("/scale 1.5 3"))
    await dispatch([module]).dispatch(event("/toggle yes"))
    await dispatch([module]).dispatch(event("/toggle 假"))

    assert calls == [
        ("count", 42),
        ("scale", 1.5, 2),
        ("scale", 1.5, 3),
        ("toggle", True),
        ("toggle", False),
    ]


async def test_signature_binds_greedy_str_and_named_defaults():
    module, calls = bind_module()

    await dispatch([module]).dispatch(event("/say  你 好   呀 "))
    await dispatch([module]).dispatch(event("/note 只有第一个"))

    assert calls == [("say", "你 好 呀"), ("note", "只有第一个", "默认")]


async def test_signature_optional_none_default_keeps_digit_as_int():
    module, calls = bind_module()

    await dispatch([module]).dispatch(event("/maybedigit 123"))
    await dispatch([module]).dispatch(event("/maybedigit abc"))

    assert calls == [("maybedigit", 123), ("maybedigit", "abc")]


async def test_signature_type_error_replies_usage_and_stops_llm():
    module, calls = bind_module()
    bot = RecordingBot()
    ev = event("/count abc", bot=bot)

    await dispatch([module]).dispatch(ev)

    assert calls == []
    assert ev._llm_stop is True
    assert "用法：/count <n: 整数>" in str(bot.sent[-1])


async def test_signature_missing_required_arg_replies_usage():
    module, calls = bind_module()
    bot = RecordingBot()
    ev = event("/scale 1.5 2 3", bot=bot)  # 多余参数被忽略，不算错

    await dispatch([module]).dispatch(ev)
    assert calls == [("scale", 1.5, 2)]

    calls.clear()
    bot2 = RecordingBot()
    ev2 = event("/scale", bot=bot2)
    await dispatch([module]).dispatch(ev2)

    assert calls == []
    assert ev2._llm_stop is True
    assert "参数不足" in str(bot2.sent[-1])
    assert "[times: 整数 = 2]" in str(bot2.sent[-1])


async def test_signature_greedy_must_be_last():
    module, calls = bind_module()
    bot = RecordingBot()
    ev = event("/broken 一段话 尾巴", bot=bot)

    await dispatch([module]).dispatch(ev)

    assert calls == []
    assert ev._llm_stop is True
    assert "必须是最后一个参数" in str(bot.sent[-1])


async def test_signature_bound_params_visible_on_invocation():
    module, _calls = bind_module()
    ev = event("/count 7")

    await dispatch([module]).dispatch(ev)

    assert ev.command.params == {"n": 7}
