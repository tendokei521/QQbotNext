"""旧群史冻结：首次只拉会话外旧消息，之后逐轮复用，不再改 system 前缀。"""

from __future__ import annotations

from types import SimpleNamespace

from app.agent.loop import prompt_prep
from app.agent.session.manager import ConversationData


async def test_frozen_group_history_fetches_once_and_passes_exclusions(monkeypatch):
    calls: list[dict] = []

    async def fake_fetch(*args, **kwargs):
        calls.append(kwargs)
        return "【当前群聊[group_id=1]】\n旧消息"

    monkeypatch.setattr(prompt_prep, "_fetch_pre_history", fake_fetch)
    session = SimpleNamespace(data=ConversationData())
    history = [
        {"message_id": "100", "time": 1000},
        {"message_id": "101", "time": 1010},
    ]

    first = await prompt_prep._load_frozen_group_history(
        SimpleNamespace(), None,
        session=session,
        session_history_all=history,
        is_private=False,
        group_id="1",
        user_id="2",
        include_pre_history=True,
        history_rounds=50,
        session_id="group_1",
        meta_flags={},
        bot=None,
    )
    second = await prompt_prep._load_frozen_group_history(
        SimpleNamespace(), None,
        session=session,
        session_history_all=history + [{"message_id": "102", "time": 1020}],
        is_private=False,
        group_id="1",
        user_id="2",
        include_pre_history=True,
        history_rounds=50,
        session_id="group_1",
        meta_flags={},
        bot=None,
    )

    assert first == "【当前群聊[group_id=1]】\n旧消息"
    assert second == first, "首次拉取后必须冻结，不能随会话变长重拉"
    assert len(calls) == 1
    assert calls[0]["exclude_ids"] == {"100", "101"}
    assert calls[0]["before_ts"] == 1000


async def test_frozen_group_history_skips_when_disabled(monkeypatch):
    async def fake_fetch(*args, **kwargs):
        raise AssertionError("关闭时不应拉取")

    monkeypatch.setattr(prompt_prep, "_fetch_pre_history", fake_fetch)
    session = SimpleNamespace(data=ConversationData())
    text = await prompt_prep._load_frozen_group_history(
        SimpleNamespace(), None,
        session=session,
        session_history_all=[],
        is_private=False,
        group_id="1",
        user_id="2",
        include_pre_history=False,
        history_rounds=50,
        session_id="group_1",
        meta_flags={},
        bot=None,
    )
    assert text == ""
