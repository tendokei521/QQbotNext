"""OneBot 工具清单与下发行为的正确性回归测试。

历史故障（本文件即为防回归）：

1. **伪工具**：清单里手写的 ``get_msg_history`` 不是任何真实 OneBot action，
   而工具名会被原样当作 action 下发，于是线上稳定返回 ``1404 不支持的API
   get_msg_history``。因此这里强制「每个条目都必须来自 OneBot 文档页」。
2. **工具名 ≠ action**：``.ocr_image`` / ``.handle_quick_operation`` 这类
   Go-CQHTTP 兼容接口的真实 action 带前导点，不能作为 OpenAI function.name，
   改名后若不同步 action，就会复现同一类 1404。因此这里强制显式别名映射。

（第三类故障「失败结果被记为成功」在执行器层，见 tests/test_llm_tool_errors.py）
"""

import re
from types import SimpleNamespace

import pytest

from app.llm.onebot_tools import tools as onebot_tools
from app.llm.onebot_tools.manifest import ONEBOT_TOOLS
from app.llm.onebot_tools.tools import build_onebot_tools, resolve_action
from app.llm.tool import ToolContext, is_error_result, sanitize_tool_name

# 上游文档站形如 https://napcat.apifox.cn/226657401e0
# （OneBot 是协议名，NapCat 是本项目使用的协议端实现；改文案时**不要改这个域名**，
#   它是真实可达的 API 文档地址，改了就是死链。）
_DOC_URL_RE = re.compile(r"^https://napcat\.apifox\.cn/\d+e0$")

GROUP_ID = 778459818
USER_ID = 10001


@pytest.fixture(autouse=True)
def _clean_poke_state():
    """戳一戳节流是模块级状态，逐用例清空，避免相互影响。"""
    onebot_tools._POKE_LAST.clear()
    yield
    onebot_tools._POKE_LAST.clear()


@pytest.fixture
def runtime():
    """最小 AgentRuntime 替身：config 只需支持 .get。"""
    return SimpleNamespace(config={"onebot_tools_enable": True, "onebot_tools_debug": False})


class _FakeBot:
    """记录实际下发的 action，并按需返回固定响应。"""

    def __init__(self, response: dict | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.response = response if response is not None else {
            "status": "ok", "retcode": 0, "data": {"ok": True},
        }

    async def call_api(self, action: str, params: dict | None = None) -> dict:
        self.calls.append((action, dict(params or {})))
        return self.response


def _group_ctx(bot, runtime) -> ToolContext:
    event = SimpleNamespace(
        event_type="message_group",
        user_id=USER_ID,
        group=SimpleNamespace(group_id=GROUP_ID),
    )
    return ToolContext(
        module=runtime,
        bot=bot,
        session_id=f"group_{GROUP_ID}",
        event=event,
        runtime=runtime,
        user_id=USER_ID,
        group_id=GROUP_ID,
    )


# ---------- 清单静态约束 ----------


def test_manifest_entries_come_from_onebot_tools_docs():
    """每个条目都必须挂在真实的 OneBot 文档页上。

    手写伪工具（如 ``get_msg_history``，doc_url 只有站点根）就是靠这条漏进来的。
    """
    offenders = [
        t["name"] for t in ONEBOT_TOOLS if not _DOC_URL_RE.match(str(t.get("doc_url", "")))
    ]
    assert offenders == []


def test_manifest_tool_names_are_openai_safe():
    """工具名必须天然合法，避免依赖 sanitize 改名（改名会连带打乱 action 映射）。"""
    for tool in ONEBOT_TOOLS:
        assert sanitize_tool_name(tool["name"]) == tool["name"]


def test_action_alias_only_when_name_is_not_a_valid_action():
    """``action`` 别名只允许出现在「真实 action 无法作为工具名」的条目上。"""
    for tool in ONEBOT_TOOLS:
        action = tool.get("action")
        if action is None:
            continue
        assert isinstance(action, str) and action
        assert action != tool["name"]
        # 真实 action 本身不能是合法 OpenAI function.name，否则应直接用同名
        assert sanitize_tool_name(action) != action


def test_bogus_history_tool_removed_and_real_ones_kept():
    names = {t["name"] for t in ONEBOT_TOOLS}
    assert "get_msg_history" not in names
    assert {"get_group_msg_history", "get_friend_msg_history"} <= names


def test_dot_prefixed_apis_keep_real_action():
    by_name = {t["name"]: t for t in ONEBOT_TOOLS}
    assert resolve_action(by_name["_ocr_image"]) == ".ocr_image"
    assert resolve_action(by_name["_handle_quick_operation"]) == ".handle_quick_operation"
    # 其余条目默认同名
    assert resolve_action(by_name["get_group_msg_history"]) == "get_group_msg_history"


# ---------- 下发行为 ----------


async def test_handler_sends_real_action(runtime):
    """工具名带下划线，真正下发的必须是带点的真实 action。"""
    bot = _FakeBot()
    ctx = _group_ctx(bot, runtime)
    specs = {spec.name: spec for spec in build_onebot_tools(runtime, ctx)}

    result = await specs["_ocr_image"].handler(ctx, {"image": "a.png"})

    assert bot.calls == [(".ocr_image", {"image": "a.png"})]
    assert "ok" in result


async def test_handler_reports_api_failure_as_error_text(runtime):
    """OneBot 返回 failed 时，工具结果必须是 error 文本（供上层判定失败）。"""
    bot = _FakeBot({"status": "failed", "retcode": 1404, "message": "不支持的API x", "data": None})
    ctx = _group_ctx(bot, runtime)
    specs = {spec.name: spec for spec in build_onebot_tools(runtime, ctx)}

    result = await specs["get_group_msg_history"].handler(ctx, {"group_id": GROUP_ID, "count": 20})

    assert is_error_result(result)
    assert "1404" in result


# ---------- 戳一戳节流（防止「每条消息都戳」） ----------


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


async def test_poke_is_throttled_per_target(runtime, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(onebot_tools, "_now", clock)
    runtime.config["poke_cooldown_seconds"] = 20

    bot = _FakeBot()
    ctx = _group_ctx(bot, runtime)
    specs = {spec.name: spec for spec in build_onebot_tools(runtime, ctx)}
    poke = specs["send_poke"]

    first = await poke.handler(ctx, {"user_id": USER_ID})
    assert not is_error_result(first)
    assert len(bot.calls) == 1

    clock.t += 5
    second = await poke.handler(ctx, {"user_id": USER_ID})
    assert is_error_result(second)
    assert "冷却中" in second
    assert len(bot.calls) == 1  # 被拦下，没有再发出去

    clock.t += 20  # 超过 20s 窗口
    third = await poke.handler(ctx, {"user_id": USER_ID})
    assert not is_error_result(third)
    assert len(bot.calls) == 2


async def test_poke_throttle_is_per_target_and_can_be_disabled(runtime, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(onebot_tools, "_now", clock)

    bot = _FakeBot()
    ctx = _group_ctx(bot, runtime)
    specs = {spec.name: spec for spec in build_onebot_tools(runtime, ctx)}
    poke = specs["send_poke"]

    await poke.handler(ctx, {"user_id": USER_ID})
    # 换一个人不共享冷却
    other = await poke.handler(ctx, {"user_id": 20002})
    assert not is_error_result(other)
    assert len(bot.calls) == 2

    # 关闭节流后可以连续戳
    runtime.config["poke_cooldown_seconds"] = 0
    again = await poke.handler(ctx, {"user_id": USER_ID})
    assert not is_error_result(again)
    assert len(bot.calls) == 3


async def test_failed_poke_does_not_consume_cooldown(runtime, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(onebot_tools, "_now", clock)
    runtime.config["poke_cooldown_seconds"] = 60

    bot = _FakeBot({"status": "failed", "retcode": 1404, "message": "不支持", "data": None})
    ctx = _group_ctx(bot, runtime)
    specs = {spec.name: spec for spec in build_onebot_tools(runtime, ctx)}
    poke = specs["send_poke"]

    await poke.handler(ctx, {"user_id": USER_ID})  # 失败
    retry = await poke.handler(ctx, {"user_id": USER_ID})  # 失败不占冷却 → 仍会真的重试

    assert "冷却中" not in retry
    assert len(bot.calls) == 2


async def test_other_tools_are_not_throttled(runtime, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(onebot_tools, "_now", clock)
    runtime.config["poke_cooldown_seconds"] = 600

    bot = _FakeBot()
    ctx = _group_ctx(bot, runtime)
    specs = {spec.name: spec for spec in build_onebot_tools(runtime, ctx)}

    for _ in range(3):
        result = await specs["get_group_member_list"].handler(ctx, {"group_id": GROUP_ID})
        assert not is_error_result(result)
    assert len(bot.calls) == 3
