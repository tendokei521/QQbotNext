"""从前端分区清单的唯一真相生成 `sections.json`。

## 为什么改成生成

v2 之前 `app/webui/dashboard/src/agent/sections.json` 是手写的 9 个分区、
160 个字段的归属表。v3 收敛到 5 页 / 43 键之后，手写必然会与
`app/agent/config_partition.py` 漂移（那才是唯一真相）。

于是：**本脚本 = 唯一写入方，`page_summary()` = 唯一数据源**，
CI 用 `--check` 校验"清单是否已重新生成"。

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

TARGET = ROOT / "app" / "webui" / "dashboard" / "src" / "agent" / "sections.json"

#: 页面 → 前端路由与图标/配色（属于展示层，故放在生成器里而不是分区表里）
PAGE_VIEW = {
    "basic": {
        "title": "基础配置",
        "subtitle": "启用 Agent、Provider 模型与人格预设",
        "icon": "mdi-tune-variant",
        "color": "primary",
        "path": "/agent/basic",
        # 定制卡片（启用 Agent / Provider 模型池 / 人格预设）排在表单之前：
        # 用户配 Agent 的动作顺序就是「开 → 给模型 → 给人格」
        "customFirst": True,
    },
    "permission": {
        "title": "权限管理",
        "subtitle": "响应权限档位、拥有者与管理者名册",
        "icon": "mdi-shield-account-outline",
        "color": "success",
        "path": "/agent/permission",
    },
    "behavior": {
        "title": "对话行为",
        "subtitle": "触发设置、消息抖动与回复打断",
        "icon": "mdi-call-split",
        "color": "secondary",
        "path": "/agent/behavior",
    },
    "session": {
        "title": "会话管理",
        "subtitle": "会话窗口、群聊历史注入与上下文压缩",
        "icon": "mdi-chat-processing-outline",
        "color": "info",
        "path": "/agent/session",
    },
    "advanced": {
        "title": "高级功能",
        "subtitle": "图片与动图理解、联网搜索",
        "icon": "mdi-wrench-cog-outline",
        "color": "warning",
        "path": "/agent/advanced",
    },
}

#: 分组图标（属于展示层）
GROUP_ICONS = {
    "basic_switch": "mdi-toggle-switch-outline",
    "basic_model": "mdi-api",
    "basic_persona": "mdi-account-voice",
    "permission_role": "mdi-account-key-outline",
    "permission_roster": "mdi-account-star-outline",
    "behavior_trigger": "mdi-at",
    "behavior_debounce": "mdi-timer-outline",
    "behavior_interrupt": "mdi-hand-back-right-outline",
    "adv_model_cache": "mdi-cached",
    "session_window": "mdi-window-open-variant",
    "session_compress": "mdi-arrow-collapse-vertical",
    "session_history": "mdi-history",
    "session_window": "mdi-timer-sand",
    "session_history": "mdi-forum-outline",
    "session_compress": "mdi-arrow-collapse-vertical",
    "adv_media": "mdi-image-multiple-outline",
    "adv_web_search": "mdi-web",
}


#: 由**定制卡片**渲染、不在 schema 表单里重复出现的字段（展示层决定，故放在生成器里）。
#: 字段的**归属**仍由分组声明——契约测试照样校验覆盖率，只是表单不再渲染第二遍。
CUSTOM_FIELDS = {
    "basic": ["prompt_preset"],
}


def build_manifest() -> dict:
    """按 `config_partition.page_summary()` 组装前端清单。"""
    from app.agent.config_partition import page_summary

    sections = []
    for page in page_summary():
        view = PAGE_VIEW[page["id"]]
        sections.append(
            {
                "id": page["id"],
                "path": view["path"],
                "title": view["title"],
                "subtitle": view["subtitle"],
                "icon": view["icon"],
                "color": view["color"],
                "order": page["order"],
                **({"customFirst": True} if view.get("customFirst") else {}),
                **(
                    {"customFields": CUSTOM_FIELDS[page["id"]]}
                    if page["id"] in CUSTOM_FIELDS
                    else {}
                ),
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
        "version": 5,
        "note": (
            "本文件由 scripts/gen_sections.py 从 app/agent/config_partition.py 生成，"
            "请勿手改。要加字段/换分组请改 config_partition.py 里的 UI_PAGES。"
            "v4：模块能力（流式/记忆/主动消息/工具/联网等）已迁到各自模块的配置页，"
            "本体只剩 5 页 43 键。v5：移除 area 字段——v4 之后只剩单一区域，"
            "单值枚举无信息量，且会让 /advanced 这类已删区域在代码里阴魂不散。"
        ),
        "generatedFrom": "app/agent/config_partition.py::page_summary()",
        "sections": sections,
    }


def render(manifest: dict) -> str:
    return json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成前端分区清单 sections.json")
    parser.add_argument("--check", action="store_true", help="只校验，不写文件")
    args = parser.parse_args(argv)

    content = render(build_manifest())
    current = TARGET.read_text(encoding="utf-8") if TARGET.is_file() else ""

    if args.check:
        if current != content:
            print(f"[fail] {TARGET.relative_to(ROOT)} 与 page_summary() 不一致")
            print("       请运行: python scripts/gen_sections.py")
            return 1
        print(f"[ok] {TARGET.relative_to(ROOT)} 已是最新")
        return 0

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(content, encoding="utf-8")
    print(f"[ok] 已生成 {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
