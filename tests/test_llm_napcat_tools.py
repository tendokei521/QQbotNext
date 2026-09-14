"""NapCat 工具清单与下发行为的正确性回归测试。

历史故障（本文件即为防回归）：

1. **伪工具**：清单里手写的 ``get_msg_history`` 不是任何真实 OneBot action，
   而工具名会被原样当作 action 下发，于是线上稳定返回 ``1404 不支持的API
   get_msg_history``。因此这里强制「每个条目都必须来自 NapCat 文档页」。
2. **工具名 ≠ action**：``.ocr_image`` / ``.handle_quick_operation`` 这类
   Go-CQHTTP 兼容接口的真实 action 带前导点，不能作为 OpenAI function.name，
   改名后若不同步 action，就会复现同一类 1404。因此这里强制显式别名映射。

（第三类故障「失败结果被记为成功」在执行器层，见 tests/test_llm_tool_errors.py）
"""

import re
from types import SimpleNamespace

import pytest

from app.llm.napcat.manifest import NAP_CAT_TOOLS
from app.llm.napcat.tools import build_napcat_tools, resolve_action
from app.llm.tool import ToolContext, is_error_result, sanitize_tool_name

# NapCat 文档页形如 https://napcat.apifox.cn/226657401e0
_DOC_URL_RE = re.compile(r"^https://napcat\.apifox\.cn/\d+e0$")

GROUP_ID = 778459818
USER_ID = 10001


@pytest.fixture
def runtime():
    """最小 AgentRuntime 替身：config 只需支持 .get。"""
    return SimpleNamespace(config={"napcat_tools_enable": True, "napcat_tools_debug": False})


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


def test_manifest_entries_come_from_napcat_docs():
    """每个条目都必须挂在真实的 NapCat 文档页上。

    手写伪工具（如 ``get_msg_history``，doc_url 只有站点根）就是靠这条漏进来的。
    """
    offenders = [
        t["name"] for t in NAP_CAT_TOOLS if not _DOC_URL_RE.match(str(t.get("doc_url", "")))
    ]
    assert offenders == []


def test_manifest_tool_names_are_openai_safe():
    """工具名必须天然合法，避免依赖 sanitize 改名（改名会连带打乱 action 映射）。"""
    for tool in NAP_CAT_TOOLS:
        assert sanitize_tool_name(tool["name"]) == tool["name"]


def test_action_alias_only_when_name_is_not_a_valid_action():
    """``action`` 别名只允许出现在「真实 action 无法作为工具名」的条目上。"""
    for tool in NAP_CAT_TOOLS:
        action = tool.get("action")
        if action is None:
            continue
        assert isinstance(action, str) and action
        assert action != tool["name"]
        # 真实 action 本身不能是合法 OpenAI function.name，否则应直接用同名
        assert sanitize_tool_name(action) != action


def test_bogus_history_tool_removed_and_real_ones_kept():
    names = {t["name"] for t in NAP_CAT_TOOLS}
    assert "get_msg_history" not in names
    assert {"get_group_msg_history", "get_friend_msg_history"} <= names


def test_dot_prefixed_apis_keep_real_action():
    by_name = {t["name"]: t for t in NAP_CAT_TOOLS}
    assert resolve_action(by_name["_ocr_image"]) == ".ocr_image"
    assert resolve_action(by_name["_handle_quick_operation"]) == ".handle_quick_operation"
    # 其余条目默认同名
    assert resolve_action(by_name["get_group_msg_history"]) == "get_group_msg_history"


# ---------- 下发行为 ----------


async def test_handler_sends_real_action(runtime):
    """工具名带下划线，真正下发的必须是带点的真实 action。"""
    bot = _FakeBot()
    ctx = _group_ctx(bot, runtime)
    specs = {spec.name: spec for spec in build_napcat_tools(runtime, ctx)}

    result = await specs["_ocr_image"].handler(ctx, {"image": "a.png"})

    assert bot.calls == [(".ocr_image", {"image": "a.png"})]
    assert "ok" in result


async def test_handler_reports_api_failure_as_error_text(runtime):
    """NapCat 返回 failed 时，工具结果必须是 error 文本（供上层判定失败）。"""
    bot = _FakeBot({"status": "failed", "retcode": 1404, "message": "不支持的API x", "data": None})
    ctx = _group_ctx(bot, runtime)
    specs = {spec.name: spec for spec in build_napcat_tools(runtime, ctx)}

    result = await specs["get_group_msg_history"].handler(ctx, {"group_id": GROUP_ID, "count": 20})

    assert is_error_result(result)
    assert "1404" in result
