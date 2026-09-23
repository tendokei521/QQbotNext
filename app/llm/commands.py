"""`#llm` 指令总表 + 前缀解析 + `#llm help` 渲染。

这里是 `#llm` 指令的**唯一总表**，三个入口共用同一个前缀常量：

- ``LlmPipeline`` 用 ``is_command`` 决定是否把消息分流给指令处理器；
- ``chat.handle`` / ``chat.handle_commands`` 用 ``parse_action`` 取动作；
- `#llm help` 用 ``COMMAND_GROUPS`` 渲染帮助，**合并转发**发送。

表与实现之间不允许漂移：``tests/test_llm_help.py`` 会逐条跑一遍表内指令，
凡是被 ``handle_commands`` 当成"未知指令"的条目都会让测试失败
（新增指令 = 改 ``COMMAND_GROUPS`` + 在 ``handle_commands`` 加分支，两处必须同时到位）。
"""

from __future__ import annotations

from typing import Any

# 指令前缀：改这一处即可全局生效（pipeline 分流 / chat.handle / handle_commands 共用）
PREFIX = "#llm"

# 合并转发节点的发送者名与 uin 兜底（NapCat 要求节点带 uin；优先用 bot 自身 QQ）
FORWARD_NAME = "指令表"
FALLBACK_UIN = "10000"

HELP_TITLE = "LLM 指令表"

# 帮助首节点的说明（前缀、生效条件、回复样式）
HELP_NOTICE: tuple[str, ...] = (
    f"前缀：{PREFIX} + 空格，例如 {PREFIX} task；单独发送 {PREFIX} 等同于 {PREFIX} help。",
    "生效范围：私聊需开启「私聊回复」，群聊需开启「群聊回复」；群聊里不需要 @ 机器人。",
    f"{PREFIX} help 是静态指令表，不依赖模型：API 密钥未配置时也可以查看。",
    "指令回复统一以「# 」开头；标注「管理员」的指令仅 Bot 拥有者可用。",
)

# 帮助正文的分组：(key, 标题, ((指令, 说明), ...))
# key 用于标注"本 bot 未启用"（见 AVAILABILITY_KEYS），无对应能力的用 "help"/"session"
COMMAND_GROUPS: tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...] = (
    (
        "help",
        "总览",
        (
            (f"{PREFIX} help", "显示本指令表（合并转发）"),
        ),
    ),
    (
        "session",
        "会话与对话",
        (
            (f"{PREFIX} task", "查看当前任务 ID 与活跃对话"),
            (f"{PREFIX} list", "列出本会话的所有对话线程"),
            (f"{PREFIX} switch <conv_id|task_id>", "切换到指定对话"),
            (f"{PREFIX} new [标题]", "开启新对话（同时触发记忆重置）"),
            (f"{PREFIX} load <task_id>", "把历史任务载入为新对话"),
            (f"{PREFIX} export [task_id]", "导出会话文本"),
            (f"{PREFIX} exit", "退出会话（历史保留）"),
            (f"{PREFIX} stop", "强制结束会话（管理员）"),
        ),
    ),
    (
        "schedule",
        "定时任务",
        (
            (f"{PREFIX} schedule", "列出当前定时任务"),
            (f"{PREFIX} schedule cancel <id>", "取消指定定时任务"),
        ),
    ),
    (
        "proactive",
        "主动消息",
        (
            (f"{PREFIX} proactive", "查看主动消息状态"),
            (f"{PREFIX} proactive <session_id>", "手动触发一次主动发言"),
        ),
    ),
    (
        "memory",
        "长期记忆",
        (
            (f"{PREFIX} memory list [--all]", "查看当前可见记忆（--all 含已下架）"),
            (f"{PREFIX} memory search <词>", "检索记忆"),
            (f"{PREFIX} memory correct <旧词> <新事实>", "纠正记忆：旧记忆下架，写入新事实"),
            (f"{PREFIX} memory deny <词|id>", "下架记忆（可恢复）"),
            (f"{PREFIX} memory confirm <词|id>", "确认记忆（上调置信度）"),
            (f"{PREFIX} memory forget <id|词>", "删除本人记忆"),
            (f"{PREFIX} memory clear", "清空本人记忆（群公共记忆不受影响）"),
            (f"{PREFIX} memory reset [hard]", "重置记忆上下文；hard 彻底清除"),
            (f"{PREFIX} memory audit [owner|all]", "记忆事件审计（管理员）"),
        ),
    ),
)

# 分组 key → ``available`` 字典的键：值为假时标题标注「本 bot 未启用」
AVAILABILITY_KEYS = {"schedule": "schedule", "proactive": "proactive", "memory": "memory"}

# 视为"显示指令表"的动作（小写比较；空串 = 只发了 `#llm`）
HELP_ACTIONS = frozenset({"", "help", "?", "-h", "--help"})


def is_command(text: str) -> bool:
    """是否是 `#llm` 指令（单独一个 `#llm` 也算，等价于 help）。"""
    text = (text or "").strip()
    return text == PREFIX or text.startswith(PREFIX + " ")


def parse_action(text: str) -> str | None:
    """取 `#llm` 之后的动作串（已 strip）；不是指令时返回 None。

    空串表示只发了 `#llm`（视为 help）。
    """
    text = (text or "").strip()
    if not is_command(text):
        return None
    return text[len(PREFIX):].strip()


def is_help_action(action: str) -> bool:
    """是否是"显示指令表"动作（大小写不敏感）。"""
    return (action or "").strip().lower() in HELP_ACTIONS


def iter_commands() -> list[tuple[str, str]]:
    """展平所有指令为 ``[(指令, 说明), ...]``（顺序即帮助里的顺序）。"""
    return [entry for _, _, entries in COMMAND_GROUPS for entry in entries]


def available_note(key: str, available: dict | None) -> str:
    """分组标题后缀：能力未启用时提示「本 bot 未启用」（无法判断时不标注）。"""
    avail_key = AVAILABILITY_KEYS.get(key)
    if not avail_key or not available:
        return ""
    state = available.get(avail_key)
    if state is None or state:
        return ""
    return "（本 bot 未启用）"


def build_help_nodes(*, uin: Any = None, available: dict | None = None) -> list[dict]:
    """构造指令表的合并转发节点：说明节点 + 每个分组一个节点。

    节点名取分组标题（QQ 的合并转发卡片按节点名分条展示），正文为
    ``指令`` + 缩进说明；每个节点一段文本，避免一屏挤成一坨。
    """
    nodes = [_text_node(HELP_TITLE, "\n".join(HELP_NOTICE), uin)]
    for key, title, entries in COMMAND_GROUPS:
        body = "\n".join(f"{cmd}\n    {desc}" for cmd, desc in entries)
        nodes.append(_text_node(f"{title}{available_note(key, available)}", body, uin))
    return nodes


def build_help_text(*, available: dict | None = None) -> str:
    """纯文本指令表（合并转发失败时的降级内容）。"""
    lines = [HELP_TITLE, *HELP_NOTICE]
    for key, title, entries in COMMAND_GROUPS:
        lines.append("")
        lines.append(f"【{title}{available_note(key, available)}】")
        lines.extend(f"  {cmd} — {desc}" for cmd, desc in entries)
    return "\n".join(lines)


def chunk_text(text: str, limit: int = 700) -> list[str]:
    """按行把长文本切成不超过 ``limit`` 的若干段（降级发送时用，不切断行）。"""
    limit = max(int(limit or 1), 1)
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in (text or "").splitlines():
        line_len = len(line) + 1
        if current and size + line_len > limit:
            chunks.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += line_len
    if current:
        chunks.append("\n".join(current))
    return chunks or [""]


def _text_node(name: str, text: str, uin: Any = None) -> dict:
    """一个文本内容的合并转发节点（格式与 bilibili_parser / recall_back 一致）。"""
    return {
        "type": "node",
        "data": {
            "name": name or FORWARD_NAME,
            "uin": str(uin or FALLBACK_UIN),
            "content": [{"type": "text", "data": {"text": text}}],
        },
    }
