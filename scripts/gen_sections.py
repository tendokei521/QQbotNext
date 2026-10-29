"""从前端分区清单的唯一真相生成两份 JSON：

- `sections.json`：**AI 设置**（原「AI 对话设置」）剩余 3 页的分区清单；
- `ai-panel.json`：**AI 对话面板** 与 **回复与权限** 两个"页面外工作台"的字段清单。

## 为什么生成

`app/agent/config_partition.py` 是字段归属的唯一真相。手写清单必然漂移，
因此：**本脚本 = 唯一写入方，`page_summary()` / `panel_summary()` / `reply_summary()`
= 唯一数据源**，CI 用 `--check` 校验"清单是否已重新生成"。

```bash
python scripts/gen_sections.py           # 写文件
python scripts/gen_sections.py --check   # 只校验，不写（CI 用）
```
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SRC = ROOT / "app" / "webui" / "dashboard" / "src" / "agent"
TARGET = SRC / "sections.json"
PANEL_TARGET = SRC / "ai-panel.json"

#: 页面 → 前端路由与图标/配色（属于展示层，故放在生成器里而不是分区表里）
PAGE_VIEW = {
    "behavior": {
        "title": "对话行为",
        "icon": "mdi-call-split",
        "color": "secondary",
        "path": "/ai/settings/behavior",
    },
    "session": {
        "title": "会话管理",
        "icon": "mdi-chat-processing-outline",
        "color": "info",
        "path": "/ai/settings/session",
    },
    "advanced": {
        "title": "高级功能",
        "icon": "mdi-wrench-cog-outline",
        "color": "warning",
        "path": "/ai/settings/advanced",
    },
}

#: 分组图标（属于展示层）
GROUP_ICONS = {
    "behavior_debounce": "mdi-timer-outline",
    "behavior_interrupt": "mdi-hand-back-right-outline",
    "session_window": "mdi-timer-sand",
    "session_history": "mdi-forum-outline",
    "session_compress": "mdi-arrow-collapse-vertical",
    "adv_media": "mdi-image-multiple-outline",
    "adv_web_search": "mdi-web",
    "adv_tool_exec": "mdi-tools",
    "adv_tool_logs": "mdi-text-box-search-outline",
    "adv_model_cache": "mdi-cached",
    # 页面外工作台（AI 对话面板 / 回复与权限）
    "panel_reply": "mdi-toggle-switch-outline",
    "panel_trigger": "mdi-at",
    "panel_persona": "mdi-account-voice",
    "reply_permission": "mdi-account-key-outline",
    "reply_roster": "mdi-account-star-outline",
    "reply_tool_approval": "mdi-shield-alert-outline",
}

#: 由**定制卡片**渲染、不在 schema 表单里重复出现的字段（展示层决定）。
#: 字段的**归属**仍由分组声明——契约测试照样校验覆盖率，只是表单不再渲染第二遍。
CUSTOM_FIELDS: dict[str, list[str]] = {}


def build_manifest() -> dict:
    """按 `config_partition.page_summary()` 组装 AI 设置的分区清单。"""
    from app.agent.config_partition import page_summary

    sections = []
    for page in page_summary():
        view = PAGE_VIEW[page["id"]]
        sections.append(
            {
                "id": page["id"],
                "path": view["path"],
                "title": view["title"],
                # 副标题只认 config_partition（唯一真相）；生成器不再保留第二份。
                "subtitle": page.get("subtitle", ""),
                "icon": view["icon"],
                "color": view["color"],
                "order": page["order"],
                "groups": [
                    {
                        "id": group["id"],
                        "label": group["label"],
                        "icon": GROUP_ICONS.get(group["id"], "mdi-folder-outline"),
                        "fields": list(group["fields"]),
                    }
                    for group in page["groups"]
                ],
            }
        )
    return {
        "version": 6,
        "note": (
            "本文件由 scripts/gen_sections.py 从 app/agent/config_partition.py 生成，"
            "请勿手改。v6：AI 对话设置拆分为「AI 对话面板（ai-panel.json）+ AI 设置（本文件）」，"
            "本文件只剩 3 页（对话行为 / 会话管理 / 高级功能）；基础配置与权限管理已分别"
            "并入面板与「回复与权限」。"
        ),
        "generatedFrom": "app/agent/config_partition.py::page_summary()",
        "sections": sections,
    }


def build_panel_manifest() -> dict:
    """组装 AI 对话面板 + 回复与权限两个工作台的字段清单。"""
    from app.agent.config_partition import panel_summary, reply_summary

    def groups_of(summary: dict) -> list[dict]:
        return [
            {
                "id": group["id"],
                "label": group["label"],
                "icon": GROUP_ICONS.get(group["id"], "mdi-folder-outline"),
                "fields": list(group["fields"]),
            }
            for group in summary["groups"]
        ]

    panel = panel_summary()
    reply = reply_summary()
    return {
        "version": 1,
        "note": (
            "本文件由 scripts/gen_sections.py 从 app/agent/config_partition.py 生成，请勿手改。"
            "panel = AI 对话面板的字段卡（另外三张卡：回复总开关、模型池、权限摘要由前端定制）；"
            "reply = 回复与权限页的字段分组。"
        ),
        "generatedFrom": (
            "app/agent/config_partition.py::panel_summary()/reply_summary()"
        ),
        "panel": {"fields": panel["fields"], "cards": groups_of(panel)},
        "reply": {"fields": reply["fields"], "groups": groups_of(reply)},
    }


def render(manifest: dict) -> str:
    return json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"


def _write_or_check(target: Path, content: str, check: bool) -> int:
    current = target.read_text(encoding="utf-8") if target.is_file() else ""
    if check:
        if current != content:
            print(f"[fail] {target.relative_to(ROOT)} 与生成器输出不一致")
            print("       请运行: python scripts/gen_sections.py")
            return 1
        print(f"[ok] {target.relative_to(ROOT)} 已是最新")
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    print(f"[ok] 已生成 {target.relative_to(ROOT)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成前端分区清单 sections.json / ai-panel.json")
    parser.add_argument("--check", action="store_true", help="只校验，不写文件")
    args = parser.parse_args(argv)

    rc = _write_or_check(TARGET, render(build_manifest()), args.check)
    if rc:
        return rc
    return _write_or_check(PANEL_TARGET, render(build_panel_manifest()), args.check)


if __name__ == "__main__":
    raise SystemExit(main())
