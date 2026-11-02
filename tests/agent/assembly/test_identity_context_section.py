"""账号身份块（identity_context）：每轮提示词的最后一个 system 块。

三行固定内容：「你的账号 / 你的拥有者 / 你的管理员」——模型据此知道
"我是哪个 QQ、谁有权管理我"。这里锁四件事：

1. 账号优先取登录 QQ，取不到退回账号 id；
2. 拥有者/管理员名册顿号拼接，空名册写「未设置」（不能整块消失）；
3. 名册允许 list 或逗号/换行字符串（配置层两种形态都出现过）；
4. 它在最终 messages 里是**最后一条 system**（紧跟 extra、在历史之前）。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.agent.assembly.assembler import PromptAssembler, PromptRequest
from app.agent.assembly.builtin_sections import build_identity_context


def _req(config: dict | None = None, bot=None, bot_id: str = "bot1"):
    values = config or {}
    runtime = SimpleNamespace(
        bot_id=bot_id,
        bot=bot,
        config=values,
        config_service=None,
    )
    return SimpleNamespace(
        runtime=runtime,
        config=values,
        cfg=lambda key, default=None: values.get(key, default),
    )


def _text(req) -> str:
    messages = build_identity_context(req)
    assert len(messages) == 1 and messages[0]["role"] == "system"
    return messages[0]["content"]


def test_uses_login_qq_and_rosters():
    text = _text(_req(
        {"agent_owner_list": ["10001"], "agent_manager_list": ["20002", "30003"]},
        bot=SimpleNamespace(bot_id=3437542570),
    ))
    assert text == "你的账号：3437542570\n你的拥有者：10001\n你的管理员：20002、30003"


def test_missing_rosters_render_as_placeholder():
    text = _text(_req({}, bot=SimpleNamespace(bot_id=3437542570)))
    assert "你的拥有者：未设置" in text
    assert "你的管理员：未设置" in text


def test_roster_accepts_comma_string():
    """配置层偶尔把 string_list 存成字符串：不能退化成整段原样输出。"""
    text = _text(_req(
        {"agent_owner_list": "10001, 10002\n10003", "agent_manager_list": ""},
        bot=SimpleNamespace(bot_id=1),
    ))
    assert "你的拥有者：10001、10002、10003" in text
    assert "你的管理员：未设置" in text


def test_account_falls_back_to_account_id_without_login():
    text = _text(_req({}, bot=None, bot_id="bot1"))
    assert text.startswith("你的账号：bot1\n")


def test_identity_context_is_the_last_system_block():
    req = PromptRequest(
        config={"system_prompt": "人设", "prompt_preset": "chat", "agent_owner_list": ["10001"]},
        user_text="hi",
        available_tools={"send_message"},
        runtime=SimpleNamespace(bot_id="bot1", bot=SimpleNamespace(bot_id=42), config={}, config_service=None),
    )
    messages = PromptAssembler().build(req)
    systems = [m for m in messages if m.get("role") == "system"]
    assert systems, "应至少有一条 system"
    last_system = str(systems[-1].get("content") or "")
    assert last_system.startswith("你的账号：42")
    assert messages[-1]["role"] == "user", "身份块之后只剩本轮 user 消息"
