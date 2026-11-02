"""上下文增强模块配置（重写版：2 键）。

设计口径见模块 main.py：
- `context_enable`：总开关（工具 + 解析约定段）；
- `prefetch_reply`：本轮引用的正文自动内联（唯一值得预取的场景）。

旧的焦点表/预取候选/歧义策略等 8 个键已删除——新格式里每条消息都带 id，
指代靠"可寻址 + 按需取回"，不再靠猜。
"""

SCHEMA = {
    "context_enable": {
        "type": "boolean",
        "label": "上下文增强",
        "description": "开启后模型能按需取回上下文（工具与解析约定）；关闭只能用窗口内消息",
        "default": True,
    },
    "prefetch_reply": {
        "type": "boolean",
        "label": "内联引用正文",
        "description": "本轮消息引用了某条时，自动把被引用内容取回并内联",
        "default": True,
        "showIf": {"key": "context_enable", "value": True},
    },
}
