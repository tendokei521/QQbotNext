"""前缀稳定性：分层装配之后，system 前缀只能因为"用户真的换了配置/方案"而变。

## 为什么要锁

KV / prompt 缓存按**字节前缀**命中：system 段里混进时间戳、随机数、遍历顺序，
每轮都会整段重算。分层重构把顺序固定成 L1→L2→L3→L4→L5、层内按 (order, name) 稳定排序，
所以"同一配置、不同用户问题"应该得到**同一个 system 前缀指纹**；换了方案/人设才该变。

这组用例同时是 ``prompt_cache`` 三个纯函数的生产级用例——它们之前只有自测。
"""

from __future__ import annotations

from app.agent.assembly.assembler import PromptAssembler, PromptRequest
from app.agent.assembly.prompt_cache import (
    is_prefix_stable,
    stable_messages_digest,
    system_prefix_fingerprint,
)


class _Config:
    def __init__(self, data: dict) -> None:
        self._data = dict(data)

    def get(self, key, default=None):
        return self._data.get(key, default)


def _build(text: str, **overrides):
    config = {"system_prompt": "人设正文", "prompt_preset": "chat", **overrides}
    return PromptAssembler().build(
        PromptRequest(config=_Config(config), user_text=text, available_tools={"send_message"})
    )


def test_system_prefix_is_stable_across_user_texts():
    """同一配置下，换一句用户问题不该动 system 前缀（否则缓存全废）。"""
    a = system_prefix_fingerprint(_build("你好"))
    b = system_prefix_fingerprint(_build("今天天气怎么样，说点别的"))
    assert a == b


def test_system_prefix_changes_when_preset_changes():
    chat = system_prefix_fingerprint(_build("你好"))
    roleplay = system_prefix_fingerprint(_build("你好", prompt_preset="roleplay"))
    assert chat != roleplay, "换方案必须换前缀，否则说明方案没生效"


def test_system_prefix_changes_when_persona_changes():
    a = system_prefix_fingerprint(_build("你好"))
    b = system_prefix_fingerprint(_build("你好", system_prompt="另一个人设"))
    assert a != b


def test_prefix_grows_by_appending_history():
    """多轮对话只在尾部追加：旧前缀必须保持是新的前缀。"""
    short = _build("第一句")
    longer = PromptAssembler().build(
        PromptRequest(
            config=_Config({"system_prompt": "人设正文", "prompt_preset": "chat"}),
            user_text="第二句",
            history=[{"role": "user", "content": "第一句"}],
            available_tools={"send_message"},
        )
    )
    assert is_prefix_stable(short, longer), "新消息只应在尾部追加"
    assert stable_messages_digest(short) != stable_messages_digest(longer)
