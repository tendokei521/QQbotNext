"""引用正文渲染：必须走组装模块的渲染器（图片占位带消息 id），不能静默退回纯文本。

回归背景：``app/agent/message_assembly/enrich.py`` 里写的是
``from .message_assembly import render_segments``——包内相对导入被解析成不存在的子模块
``app.agent.message_assembly.message_assembly``，每次都抛 ImportError，被 ``except``
吞成一条 debug 日志。后果：引用消息里的图片只能渲染成 ``[image:id=<file hash>]``
（纯文本兜底），而不是 ``[image:id=<消息id>]``，模型拿不到能展开的锚点。

这个用例锁的是**对外的占位契约**（图片段渲染出消息 id），不是实现快照。
"""

from __future__ import annotations

from app.agent.message_assembly.enrich import _render_reply_text


def test_reply_render_uses_message_id_for_image_placeholder():
    rendered = _render_reply_text(
        [{"type": "image", "data": {"file": "abc.jpg"}}], message_id="1003"
    )
    assert rendered == "[image:id=1003]"


def test_reply_render_keeps_text_and_mixed_segments():
    rendered = _render_reply_text(
        [
            {"type": "text", "data": {"text": "看图"}},
            {"type": "image", "data": {"file": "abc.jpg"}},
        ],
        message_id="1004",
    )
    assert rendered == "看图[image:id=1004]"
