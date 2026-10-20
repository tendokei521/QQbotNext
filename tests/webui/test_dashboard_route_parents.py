"""父级路由组件必须用 ``<router-view>`` 渲染子路由，不能用 ``<slot>``。

``AgentLayout`` 曾写成 ``<slot v-else />``：它作为路由组件时，``<slot>`` 拿到的是
「父组件传入的插槽内容」，而路由渲染不给任何插槽内容，于是子路由被静默吞掉，
只留下一个 ``<!---->`` 注释 —— 表现是整个内容区一片空白，且

* 路由表正确（``matched`` 里三条记录齐全）；
* 懒加载 chunk 全部 200；
* 控制台零报错。

这类bug排查成本极高，而静态检查只要一行就能拦住，故单独守住。
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_SRC = ROOT / "app" / "webui" / "dashboard" / "src"

#: 允许有子路由的父级路由组件（新增父级路由时请在此登记）
ROUTE_PARENT_COMPONENTS = (
    "App.vue",               # 根容器
    "layouts/DefaultLayout.vue",
    "layouts/AgentLayout.vue",
    "views/ToolsPage.vue",   # /tools 下的 system / onebot / mcp / module
)


@pytest.mark.parametrize("relative", ROUTE_PARENT_COMPONENTS)
def test_route_parent_renders_child_route(relative: str):
    path = DASHBOARD_SRC / relative
    assert path.is_file(), f"父级路由组件不存在: {path}"
    content = path.read_text(encoding="utf-8")
    assert "<router-view" in content, (
        f"{relative} 是父级路由组件，但没有 <router-view>，子路由会被静默吞掉（内容区空白）。"
        "若用了 <slot />，改成 <router-view />。"
    )
