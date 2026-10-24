"""`.env` 配置模板：**唯一来源**。

两处产物都由本文件渲染出来，避免"模板写着 A、代码读着 B"的漂移：

- 首次启动时生成的完整 `.env`（`render_env`）——见 `app/core/webui_token.py` 的
  `ensure_env_file`；
- 仓库里的 `.env.example`（`render_example`）——手工拷贝用的参考模板。

## 三个约定

1. **只登记真正被消费的键**。`Settings` 里的字段与本名单必须严格一一对应
   （`tests/core/test_env_template.py` 守门）：加了配置字段却没登记 → 测试红；
   登记了没人读的键 → 也是测试红，不会再有 `DEBUG` 这种"写了没用"的摆设。
2. **不是所有环境变量都该进 `.env`**。`.env` 只喂 `pydantic-settings`（`Settings`
   字段），**不会**进入 `os.environ`；像 `QQBOT_LLM_DATA_DIR` 这种在
   `app/infrastructure/fs/paths.py` 里直接 `os.environ.get()` 的开关只能作为 shell
   环境变量存在，写进 `.env` 是无效的，故不登记。
3. **密码类键（`secret=True`）在模板里留空**，生成的 `.env` 里才填真实值。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.settings import ROOT_DIR, Settings


@dataclass(frozen=True)
class EnvKey:
    """一个 `.env` 键：变量名 + 模板示例值 + 上方注释 + 是否敏感。"""

    name: str
    example: str
    comment: str
    secret: bool = False


#: 分节登记表（顺序即 `.env` 里的书写顺序）
ENV_SECTIONS: tuple[tuple[str, tuple[EnvKey, ...]], ...] = (
    ("WebUI", (
        EnvKey(
            "WEBUI_HOST", "127.0.0.1",
            "WebUI 监听地址；建议仅本机访问，对外暴露请走反向代理并设置令牌",
        ),
        EnvKey("WEBUI_PORT", "9200", "WebUI 端口"),
        EnvKey(
            "WEBUI_TOKEN", "",
            "WebUI 访问令牌：留空则首次启动自动生成并写回本文件，登录时粘贴该值"
            "（令牌只打印在控制台，不落日志）",
            secret=True,
        ),
        EnvKey(
            "WEBUI_AUTO_OPEN", "false",
            "启动后用浏览器打开带令牌的一键登录链接"
            "（本地 start 脚本默认开启，可用 --no-open 关闭）",
        ),
    )),
    ("数据 / 日志", (
        EnvKey(
            "DB_PATH", "data/app.db",
            "SQLite 数据库文件（相对项目根目录；账号、模块配置、WebUI 偏好全在这里）",
        ),
        EnvKey("LOG_DIR", "logs", "日志目录（WebUI 日志面板与 /log 指令都读它）"),
    )),
    ("Bot 连接（OneBot / NapCat）", (
        EnvKey("WS_CONNECT_TIMEOUT", "30", "首次连接超时（秒）"),
        EnvKey("WS_PING_INTERVAL", "30", "心跳间隔（秒）"),
        EnvKey("WS_PING_TIMEOUT", "10", "心跳超时（秒）"),
    )),
    ("插件 / 模块目录", (
        EnvKey(
            "MODULE_DIR", "data/module",
            "插件运行时数据根目录（configs / data / plugins(zip) / uninstalled_modules.json）",
        ),
        EnvKey(
            "PLUGINS_DIR", "app/plugins",
            "业务插件**代码**目录；改这里可以整体换一套插件集",
        ),
    )),
)

#: 模板覆盖的全部键名（顺序与文件一致）
TEMPLATE_KEYS: tuple[str, ...] = tuple(
    key.name for _title, keys in ENV_SECTIONS for key in keys
)

_EXAMPLE_HEADER: tuple[str, ...] = (
    "# ============================================================",
    "# QQBot Next 配置模板",
    "#",
    "# 用法：复制为 .env 后按需修改；变量名 = app/core/settings.py 的字段名大写。",
    "# 首次启动若没有 .env，程序会自动生成一份**完整**配置（含随机令牌），",
    "# 所以本文件只是参考，漏掉某个键也不影响启动。",
    "# ============================================================",
)

_GENERATED_HEADER: tuple[str, ...] = (
    "# ============================================================",
    "# QQBot Next 配置（首次启动自动生成）",
    "#",
    "# 变量名 = app/core/settings.py 的字段名大写；改完需重启生效。",
    "# 环境变量优先于本文件：同名环境变量存在时，这里的值不生效。",
    "# ============================================================",
)


def _value_text(value: Any) -> str:
    """把设置值渲染成 `.env` 里的一行值。

    路径尽量写成相对项目根目录的形式（`data/app.db` 比绝对路径更适合提交/迁移）；
    落在项目外的路径保持绝对形式。
    """
    if isinstance(value, Path):
        try:
            return value.relative_to(ROOT_DIR).as_posix()
        except ValueError:
            return value.as_posix()
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _render(header: tuple[str, ...], value_of: Callable[[EnvKey], str]) -> str:
    lines: list[str] = list(header)
    for title, keys in ENV_SECTIONS:
        lines += ["", f"# ==================== {title} ===================="]
        for key in keys:
            lines.append(f"# {key.comment}")
            lines.append(f"{key.name}={value_of(key)}")
    return "\n".join(lines) + "\n"


def render_example() -> str:
    """`.env.example` 的内容（密码类键留空）。"""
    return _render(_EXAMPLE_HEADER, lambda key: "" if key.secret else key.example)


def render_env(settings: Settings, *, token: str | None = None) -> str:
    """首启生成的 `.env` 内容：模板注释 + **当前生效值**。

    `token` 显式传入时才写令牌（调用方据此决定"生成并落盘"还是"环境变量优先，
    文件里留空"）；`None` 表示沿用 `settings.webui_token`。
    """
    def value_of(key: EnvKey) -> str:
        if key.secret:
            return (settings.webui_token or "") if token is None else token
        return _value_text(getattr(settings, key.name.lower()))

    return _render(_GENERATED_HEADER, value_of)


def write_example(path: Path | None = None) -> Path:
    """按模板重写 `.env.example`（仓库维护用：`python -m app.core.env_template`）。"""
    target = path or (ROOT_DIR / ".env.example")
    target.write_text(render_example(), encoding="utf-8", newline="\n")
    return target


if __name__ == "__main__":  # pragma: no cover - 维护入口
    print(f"已按模板重写: {write_example()}")
