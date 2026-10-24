"""`.env` 的首次生成 + WebUI 访问令牌的自动生成与持久化。

## 为什么改成「空则自动生成」

历史行为是「`WEBUI_TOKEN` 为空 = 完全不启用鉴权」：`create_app()` 见到空令牌就
直接跳过中间件注册，整个后台变全裸。默认只绑 `127.0.0.1` 时问题不大，但只要用户
把 `WEBUI_HOST` 改成 `0.0.0.0` 又忘了填令牌，同网段任何人都能读到 Agent 配置、
OneBot 账号的 `access_token` 明文，以及能直接下发插件安装。

现在改为「空则自动生成」：首次启动生成一个随机令牌写回 `.env`，重启后保持不变
（写回文件而不是只放内存，否则每次重启都换新令牌，等于每次都要重新登录）。

## 两个入口

- `ensure_env_file`：启动时调用。**没有 `.env`** → 按 `app/core/env_template.py`
  生成一份完整配置（所有键 + 注释 + 随机令牌）；**已有 `.env`** → 只补空令牌，
  其余内容一字不动。
- `ensure_webui_token`：只管令牌（`.env` 已存在时的补写路径，也被上面的入口复用）。

## 两个硬性边界

1. **只在真实启动路径（`bootstrap.run`）调用**，绝不放进 `create_app()` /
   `build_container()`：测试到处直接 `create_app(container)` 且 `conftest.py` 显式传
   `webui_token=""`，若在那里生成会让全部 WebUI 测试变 401。
2. **令牌值只打印到控制台，绝不进日志文件**。所以下面用 `print()` 而不是
   `logger.info()`：本项目的 logger 把同一条记录分发给 consoles + 多个文件 handler，
   没有「只上控制台」的级别通道，只有绕过 logging 才能保证 `logs/*.log` 不留明文。
"""

from __future__ import annotations

import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from app.core.env_template import render_env
from app.core.settings import ROOT_DIR, Settings

#: 令牌熵字节数（`token_urlsafe(32)` → 43 个 URL 安全字符，约 256 bit）
TOKEN_BYTES = 32

#: `.env` 里的行首键名；只匹配行首，避免误伤注释里提到 `WEBUI_TOKEN` 的行
_ENV_KEY_RE = re.compile(r"^([ \t]*WEBUI_TOKEN[ \t]*=)[^\r\n]*", re.MULTILINE)


@dataclass(frozen=True)
class TokenState:
    """一次「确保令牌可用」的结果，供启动横幅展示。"""

    token: str
    #: 本次是否新生成（False = 复用了已有配置）
    generated: bool
    #: 生成结果是否成功落盘（False 意味着重启后令牌会变）
    persisted: bool
    #: 实际读写的 `.env` 路径
    env_path: Path


@dataclass(frozen=True)
class EnvState:
    """一次「确保 `.env` 可用」的结果（`TokenState` + 是否新建了整个文件）。"""

    token: str
    generated: bool
    persisted: bool
    env_path: Path
    #: 本次是否**新建**了完整 `.env`（首次启动）
    created: bool = False


def resolve_env_path(env_path: Path | None = None) -> Path:
    """定位 `.env`：优先当前工作目录（与 pydantic-settings 的 `env_file=".env"` 一致），
    回退到仓库根目录。

    两者可能不一致——用户从别的目录 `python <repo>/main.py` 启动时，pydantic 读不到
    仓库根的 `.env`。这里优先沿用 pydantic 的口径，保证「写进去的」就是「下次读到的」。
    """
    if env_path is not None:
        return env_path
    cwd_env = Path.cwd() / ".env"
    if cwd_env.is_file():
        return cwd_env
    return ROOT_DIR / ".env"


def _atomic_write(env_path: Path, text: str) -> None:
    """先写同目录临时文件再 `os.replace`，避免写一半导致 `.env` 损坏。"""
    env_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = env_path.parent / (env_path.name + ".tmp")
    try:
        tmp_path.write_bytes(text.encode("utf-8"))
        os.replace(tmp_path, env_path)
    finally:
        # os.replace 成功后 tmp 已不存在；失败时清理残留，别把 .tmp 留在仓库里
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _write_token(env_path: Path, token: str) -> None:
    """把令牌写回 `.env`，保留文件其余内容与换行风格。

    - 已有 `WEBUI_TOKEN=...` 行 → 原地替换值（注释、其它配置一律不动）；
    - 没有该行（或整个文件不存在）→ 追加到末尾；
    - 先写同目录临时文件再 `os.replace`，避免写一半导致 `.env` 损坏。
    """
    try:
        raw = env_path.read_bytes().decode("utf-8-sig")
    except FileNotFoundError:
        raw = ""

    newline = "\r\n" if "\r\n" in raw else "\n"
    if _ENV_KEY_RE.search(raw):
        text = _ENV_KEY_RE.sub(lambda m: m.group(1) + token, raw, count=1)
    else:
        if raw and not raw.endswith(("\n", "\r")):
            raw += newline
        text = f"{raw}WEBUI_TOKEN={token}{newline}"

    _atomic_write(env_path, text)


def ensure_env_file(settings: Settings, env_path: Path | None = None) -> EnvState:
    """确保 `.env` 存在且令牌可用。

    - **`.env` 不存在（首次启动）**：按 `app/core/env_template.py` 生成一份**完整**配置
      ——所有被读取的键连同注释一起写进去，用户想调什么就直接改，不必再去对照
      `.env.example` 一个个补；
    - **`.env` 已存在**：只补空的 `WEBUI_TOKEN`，其余键与注释一律不动
      （绝不覆盖用户手改过的值，也不擅自追加新键）。

    写盘失败不抛异常（仓库目录只读等场景）：降级为「仅本进程内存生效」，由调用方提示。
    """
    target = resolve_env_path(env_path)
    if target.is_file():
        state = ensure_webui_token(settings, env_path=target)
        return EnvState(
            token=state.token,
            generated=state.generated,
            persisted=state.persisted,
            env_path=state.env_path,
            created=False,
        )

    token = (settings.webui_token or "").strip()
    generated = False
    if not token:
        token = secrets.token_urlsafe(TOKEN_BYTES)
        generated = True
    # 环境变量优先于 .env：此时令牌不落盘（写进去也读不到），文件里该行留空
    env_override = "WEBUI_TOKEN" in os.environ
    try:
        _atomic_write(target, render_env(settings, token="" if env_override else token))
        created = True
    except OSError:
        created = False
    settings.webui_token = token
    return EnvState(
        token=token,
        generated=generated,
        # 「已固定」= 文件确实写成了、且令牌不是被环境变量覆盖的空串
        persisted=created and not env_override,
        env_path=target,
        created=created,
    )


def ensure_webui_token(settings: Settings, env_path: Path | None = None) -> TokenState:
    """确保 `settings.webui_token` 非空：为空则生成随机令牌、写回 `.env`、并就地更新 settings。

    就地更新 `settings` 是关键：`bootstrap.run` 拿到的实例随后被 `build_container`
    注册进容器，WebUI 中间件与 WS 端点都从容器取这个实例，所以在这里赋值即可让本次
    进程立即生效，无需重启。

    写盘失败**不抛异常**（例如仓库目录只读）：降级为「仅本进程内存生效」并把
    `persisted=False` 交给调用方提示用户，而不是让整个 Bot 起不来。
    """
    token = (settings.webui_token or "").strip()
    target = resolve_env_path(env_path)

    if token:
        return TokenState(token=token, generated=False, persisted=True, env_path=target)

    token = secrets.token_urlsafe(TOKEN_BYTES)
    # 环境变量优先于 .env（pydantic-settings 的既定行为）。若用户显式设了空串
    # `WEBUI_TOKEN=`，写 .env 也不会被读到，重启必换令牌——这种情况必须如实上报。
    env_override = "WEBUI_TOKEN" in os.environ
    persisted = not env_override
    if not env_override:
        try:
            _write_token(target, token)
        except OSError:
            persisted = False

    settings.webui_token = token
    return TokenState(token=token, generated=True, persisted=persisted, env_path=target)


def local_url(host: str, port: int) -> str:
    """给用户点/打开的地址：`0.0.0.0`、`::` 这类监听地址无法直接访问，回落到回环地址。"""
    shown_host = "127.0.0.1" if host in ("", "0.0.0.0", "::") else host
    return f"http://{shown_host}:{port}"


def access_url(host: str, port: int, token: str) -> str:
    """带令牌的一键登录地址。

    服务端收到 `?accesstoken=` 且校验通过后，会换成 HttpOnly 会话 cookie 并
    303 重定向到不含令牌的干净地址（见 `webui/auth.py` 与 `webui/app.py`），
    所以令牌不会留在地址栏里。
    """
    return f"{local_url(host, port)}/?accesstoken={quote(token, safe='')}"


def print_webui_token_banner(state: TokenState | EnvState, host: str, port: int) -> None:
    """把访问令牌打到**控制台**（stdout），刻意不走 logger、不落日志文件。"""
    bar = "=" * 68
    lines = ["", bar]
    if state.generated:
        lines.append(" WebUI 访问令牌：本次已自动生成")
    else:
        lines.append(" WebUI 访问令牌：来自配置")
    lines += [
        "",
        f"    {state.token}",
        "",
        f"    地址: {local_url(host, port)}/",
        f"    一键登录: {access_url(host, port, state.token)}",
    ]
    if getattr(state, "created", False):
        lines.append(f"    已生成配置: {state.env_path}（完整配置都在里面，改完重启生效）")
    elif state.generated:
        if state.persisted:
            lines.append(f"    已写入: {state.env_path}（想换令牌就改这里，然后重启）")
        elif "WEBUI_TOKEN" in os.environ:
            lines.append("    警告: 检测到环境变量 WEBUI_TOKEN 为空，它优先于 .env，本令牌仅在本次运行有效！")
            lines.append("          想固定下来：删掉该环境变量，或在其中填入本次令牌值。")
        else:
            lines.append("    警告: 写入 .env 失败，本令牌仅在本次运行有效，重启后会变！")
    lines += [bar, ""]
    # flush：保证这坨提示紧邻启动日志出现，不被 stdout 缓冲拖到后面
    print("\n".join(lines), flush=True)