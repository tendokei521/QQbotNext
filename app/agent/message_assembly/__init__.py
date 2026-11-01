"""消息组装（重写版）。

对外只暴露渲染与段解析；旧的 `assembly/structured.py`、`context/group.py` 的
`extract_msg_text`、`session/model.py` 的历史渲染分支在迁移完成后删除。
"""

from __future__ import annotations

from .render import render_group_event, render_history_entry, render_turn
from .segments import normalize_forward, render_forward_nodes, render_segments
from .spec import EXPANDERS, PLACEHOLDERS, TIME_FORMAT, escape_body, format_time, marker, quote

__all__ = [
    "EXPANDERS",
    "PLACEHOLDERS",
    "TIME_FORMAT",
    "escape_body",
    "format_time",
    "marker",
    "normalize_forward",
    "quote",
    "render_forward_nodes",
    "render_group_event",
    "render_history_entry",
    "render_segments",
    "render_turn",
]
