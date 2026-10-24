"""日志发送插件的业务逻辑：把「本轮次」的日志文件取出来发给提问的人。

## 两条命令

- ``/log``  → 只发 ``user.log``（发给对方时叫 ``user.txt``）
- ``/logs`` → 发 debug / warn / errors / user 四个文件

## 「本轮次」指的是哪一份

日志按 6 小时轮转（见 `app/core/logger.py`）：**正在写的**四个文件躺在 ``logs/`` 根目录，
轮转出去的旧段进 ``logs/YYYY-MM-DD-HH/``。所以「本轮次 = ``logs/`` 根目录下那四个文件」，
不需要额外记账，目录本身就把「当前段」和「历史段」分开了。

## 为什么落成 .txt 副本

1. ``.log`` 在手机 QQ 里不好预览，``.txt`` 点开就能看；
2. 不把**正在被写**的活文件交出去（发送期间 handler 还在往里追加）；
3. 顺手裁剪：默认只保留末尾 N 行 / 不超过 N MB，免得 30 MB 的 debug.log 把上传拖死。

## 发送策略（逐级降级）

1. ``upload_group_file`` / ``upload_private_file``：正常文件消息（NapCat 等实现都认）；
2. 失败 → ``file`` 消息段（部分 OneBot 实现只认这个）；
3. 还有文件没发出去 → **合并转发**（``send_forward_msg``）：一个结果清单节点 + 每个失败
   文件末尾若干行的正文节点（超长按行拆节点）。纯文本兜底改走这里——一张卡片带过去，
   比在聊天里糊一大坨文本好读，也不刷屏；
4. 合并转发也失败 → 极端兜底：纯文本贴末尾几行（保证「至少能看到东西」）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from app.core.logger import module_logger, account_label

#: 与 `app/webui/api/logs.py` 的 LOG_FILENAMES 保持一致（同一套轮转产物）
LOG_FILENAMES = ("debug.log", "warn.log", "errors.log", "user.log")
USER_LOG_FILENAME = "user.log"

#: 两条命令的业务取值（解析由框架的命令节点完成，这里只是"要哪几个文件"的开关）
COMMAND_USER_LOG = "log"
COMMAND_ALL_LOGS = "logs"

#: 默认只发末尾 2000 行（0 = 全量，全量仍受 max_file_mb 约束）
DEFAULT_TAIL_LINES = 2000
DEFAULT_MAX_FILE_MB = 8.0
#: 文件消息发不出去时，合并转发里每个文件带多少行正文
DEFAULT_FORWARD_LINES = 200
#: 合并转发也失败时的极端兜底：在纯文本里贴多少行
DEFAULT_FALLBACK_TEXT_LINES = 30
#: 落盘副本的存活时间：留一点余量给 OneBot 侧读文件，别边发边删
OUTBOX_MAX_AGE_SECONDS = 600


@dataclass
class LogSnapshot:
    """一份待发送的日志副本（.txt）。"""

    path: Path
    name: str
    size: int
    source: Path
    truncated: bool
    lines: int


@dataclass
class SendResult:
    """单个文件的发送结果。"""

    snapshot: LogSnapshot
    ok: bool
    detail: str


# ==================== 取文件 ====================


def select_log_files(log_dir: Path, command: str) -> list[Path]:
    """按命令挑出本轮次已有的日志文件（不存在的跳过）。"""
    names = (USER_LOG_FILENAME,) if command == COMMAND_USER_LOG else LOG_FILENAMES
    existing: list[Path] = []
    for name in names:
        path = Path(log_dir) / name
        if path.is_file():
            existing.append(path)
    return existing


def _read_tail(src: Path, max_bytes: int) -> tuple[bytes, bool]:
    """读文件（超限只读末尾 max_bytes 字节，并丢掉被切断的首行）。"""
    size = src.stat().st_size
    if max_bytes <= 0 or size <= max_bytes:
        return src.read_bytes(), False
    with src.open("rb") as f:
        f.seek(size - max_bytes)
        data = f.read()
    cut = data.find(b"\n")
    if cut != -1:
        data = data[cut + 1 :]
    return data, True


def build_copy(
    src: Path,
    out_dir: Path,
    *,
    tail_lines: int = 0,
    max_bytes: int = 0,
) -> LogSnapshot:
    """把日志文件做成待发送的 .txt 副本（按行 / 按字节裁剪，保留末尾）。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    original = src.stat().st_size
    data, truncated = _read_tail(src, max_bytes)
    lines = data.decode("utf-8", errors="replace").splitlines()
    if tail_lines > 0 and len(lines) > tail_lines:
        lines = lines[-tail_lines:]
        truncated = True

    body = "\n".join(lines)
    header = ""
    if truncated:
        header = f"# {src.name} 已截断：仅保留末尾 {len(lines)} 行（原文件 {original} 字节）\n"
    content = header + body + ("\n" if body else "")

    dest = out_dir / f"{src.stem}.txt"
    dest.write_text(content, encoding="utf-8")
    return LogSnapshot(
        path=dest,
        name=dest.name,
        size=dest.stat().st_size,
        source=src,
        truncated=truncated,
        lines=len(lines),
    )


def outbox_dir(module) -> Path | None:
    """副本落盘目录：``<module_data_dir>/<模块名>/outbox``（没有设置就返回 None）。"""
    services = getattr(getattr(module, "ctx", None), "services", None)
    settings = getattr(services, "settings", None)
    base = getattr(settings, "module_data_dir", None)
    if base is None:
        return None
    return Path(base) / str(getattr(module, "module_name", "log_sender")) / "outbox"


def log_dir_of(module) -> Path | None:
    """当前日志目录（``Settings.log_dir``）。"""
    services = getattr(getattr(module, "ctx", None), "services", None)
    settings = getattr(services, "settings", None)
    log_dir = getattr(settings, "log_dir", None)
    return Path(log_dir) if log_dir is not None else None


def purge_outbox(out_dir: Path, max_age_seconds: float = OUTBOX_MAX_AGE_SECONDS) -> None:
    """清掉过期的副本（尽力而为，失败不抛）。"""
    try:
        deadline = time.time() - max_age_seconds
        for item in Path(out_dir).iterdir():
            if item.is_file() and item.stat().st_mtime < deadline:
                item.unlink()
    except OSError:
        pass


def file_uri(path: str | Path) -> str:
    """本地路径转 OneBot 认的 ``file:///`` URI（Windows / POSIX 通吃）。"""
    posix = str(Path(path).resolve()).replace("\\", "/").lstrip("/")
    return f"file:///{posix}"


# ==================== 发送 ====================


def _is_ok(response) -> bool:
    return isinstance(response, dict) and response.get("status") == "ok"


def _upload_request(event, path: Path, name: str) -> tuple[str | None, dict]:
    """按会话类型挑上传 API 与参数；定位不到会话时返回 (None, {})。"""
    group = getattr(event, "group", None)
    group_id = getattr(group, "group_id", None) or getattr(event, "group_id", None)
    message_type = getattr(event, "message_type", "") or ""
    if group_id and message_type != "private":
        return "upload_group_file", {"group_id": int(group_id), "file": str(path), "name": name}
    user_id = getattr(event, "user_id", None)
    if user_id:
        return "upload_private_file", {"user_id": int(user_id), "file": str(path), "name": name}
    return None, {}


async def send_log_file(event, path: Path, name: str) -> tuple[bool, str]:
    """发一个日志文件：先试上传 API，再试 ``file`` 消息段。返回 (成功, 说明)。"""
    detail = "未找到可用的发送通道"
    bot = getattr(event, "bot", None)
    action, params = _upload_request(event, path, name)
    if bot is not None and action is not None and hasattr(bot, "call_api"):
        try:
            response = await bot.call_api(action, params)
        except Exception as e:  # noqa: BLE001 - 发送失败要降级，不能把事件处理拖崩
            detail = f"{action} 异常: {e}"
        else:
            if _is_ok(response):
                return True, action
            retcode = response.get("retcode") if isinstance(response, dict) else None
            detail = f"{action} 未成功{'' if retcode is None else f'（retcode={retcode}）'}"

    segment = {"type": "file", "data": {"file": file_uri(path), "name": name}}
    try:
        response = await event.reply([segment])
    except Exception as e:  # noqa: BLE001
        return False, f"{detail}；file 段异常: {e}"
    if _is_ok(response):
        return True, "file 段"
    return False, detail


# ==================== 合并转发（纯文本兜底改走这里） ====================

#: 合并转发卡片上显示的发送者名
FORWARD_NAME = "日志发送"
#: 无可用 uin 时的兜底（OneBot 要求节点带 uin）
FORWARD_FALLBACK_UIN = "10000"
#: 单个转发节点正文的软上限：超了就拆成多条节点（卡片不会糊成一屏）
FORWARD_NODE_CHARS = 1200


def _text_node(name: str, text: str, uin=None) -> dict:
    """一个文本节点（格式与 bilibili_parser / recall_back 一致）。"""
    return {
        "type": "node",
        "data": {
            "name": name or FORWARD_NAME,
            "uin": str(uin or FORWARD_FALLBACK_UIN),
            "content": [{"type": "text", "data": {"text": text}}],
        },
    }


def _chunk_lines(text: str, limit: int = FORWARD_NODE_CHARS) -> list[str]:
    """按行把正文切成不超过 ``limit`` 的若干段（不切断行）。"""
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in (text or "").splitlines():
        line_len = len(line) + 1
        if current and size + line_len > max(int(limit), 1):
            chunks.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += line_len
    if current:
        chunks.append("\n".join(current))
    return chunks or [""]


def build_forward_nodes(
    results: list[SendResult],
    *,
    uin=None,
    tail_lines: int = 200,
) -> list[dict]:
    """把「发送结果 + 没发出去的文件正文」拼成合并转发节点。

    第 0 个节点是结果清单（谁成功、谁失败、失败原因），其后每个失败文件按
    ``tail_lines`` 取末尾若干行作为正文节点（超长按行拆节点）。文件消息发不出去时，
    日志正文就靠这张合并转发卡片带过去——比在聊天里贴一大坨纯文本好读，也不刷屏。
    """
    nodes = [_text_node(FORWARD_NAME, _summary(results, preview_lines=0), uin)]
    for item in results:
        if item.ok:
            continue
        text = _preview_lines(item.snapshot, tail_lines)
        if not text:
            continue
        chunks = _chunk_lines(text)
        for index, chunk in enumerate(chunks, start=1):
            name = item.snapshot.name if len(chunks) == 1 else f"{item.snapshot.name} ({index}/{len(chunks)})"
            nodes.append(_text_node(name, chunk, uin))
    return nodes


async def send_forward(event, nodes: list[dict]) -> tuple[bool, str]:
    """发一条合并转发：先走 ``send_forward_msg``，再试 node 消息段。返回 (成功, 说明)。"""
    if not nodes:
        return False, "没有可发送的内容"

    bot = getattr(event, "bot", None)
    group = getattr(event, "group", None)
    group_id = getattr(group, "group_id", None) or getattr(event, "group_id", None)
    user_id = getattr(event, "user_id", None)
    detail = "未找到可用的转发通道"

    sender = getattr(bot, "send_forward_msg", None)
    if callable(sender) and (group_id or user_id):
        try:
            if getattr(event, "message_type", "") == "private" or not group_id:
                response = await sender(user_id=user_id or 0, msgdata=nodes)
            else:
                response = await sender(group_id=group_id or 0, msgdata=nodes)
        except Exception as e:  # noqa: BLE001
            detail = f"send_forward_msg 异常: {e}"
        else:
            if _is_ok(response):
                return True, "合并转发"
            detail = "send_forward_msg 未成功"

    # 部分实现只认「node 消息段」形式
    try:
        response = await event.reply(nodes)
    except Exception as e:  # noqa: BLE001
        return False, f"{detail}；node 段异常: {e}"
    if _is_ok(response):
        return True, "合并转发（node 段）"
    return False, detail


# ==================== 配置读取 ====================


def _cfg(module, key: str, default):
    try:
        value = module.config.get(key, default)
    except Exception:  # noqa: BLE001 - 配置读失败一律走默认值
        return default
    return default if value is None else value


def _num_cfg(module, key: str, default: float) -> float:
    try:
        return float(_cfg(module, key, default))
    except (TypeError, ValueError):
        return default


def _preview_lines(snapshot: LogSnapshot, limit: int) -> str:
    """读副本末尾若干行（合并转发正文 / 极端兜底纯文本共用）。"""
    if limit <= 0:
        return ""
    try:
        text = snapshot.path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = text.splitlines()[-limit:]
    return "\n".join(lines)


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB"):
        if value < 1024 or unit == "MB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} MB"


# ==================== 入口（由 @command 调用） ====================


async def send_logs(module, event, *, command: str = "log") -> None:
    """命令入口：生成副本 → 发送 → 回一条汇总。

    解析（``/log`` 前缀、大小写、别名）与「命中即截断 LLM」都由框架的命令节点负责，
    这里只管业务。``command`` 取 ``"log"``（只发 user）或 ``"logs"``（四个都发）。
    """
    logger = module_logger.add_info(account_label(getattr(module, 'bot_id', ''))).add_info(
        getattr(module, "name", "日志发送")
    )

    if not _cfg(module, "enable", True):
        return

    log_dir = log_dir_of(module)
    if log_dir is None:
        await event.reply("⚠️ 取不到日志目录（settings.log_dir 未装配）")
        return

    files = select_log_files(log_dir, command)
    if not files:
        await event.reply(f"⚠️ 本轮次还没有日志文件（{log_dir}）")
        return

    out_dir = outbox_dir(module)
    if out_dir is None:
        await event.reply("⚠️ 插件数据目录不可用，无法准备日志文件")
        return
    purge_outbox(out_dir)

    tail_lines = int(_num_cfg(module, "tail_lines", DEFAULT_TAIL_LINES))
    max_bytes = int(_num_cfg(module, "max_file_mb", DEFAULT_MAX_FILE_MB) * 1024 * 1024)

    results: list[SendResult] = []
    for src in files:
        try:
            snapshot = build_copy(src, out_dir, tail_lines=tail_lines, max_bytes=max_bytes)
        except OSError as e:
            logger.warning(f"日志副本生成失败 {src.name}: {e}")
            continue
        ok, detail = await send_log_file(event, snapshot.path, snapshot.name)
        if not ok:
            logger.warning(f"日志发送失败 {snapshot.name}: {detail}")
        results.append(SendResult(snapshot=snapshot, ok=ok, detail=detail))

    if not results:
        await event.reply("⚠️ 日志文件读取失败（详见 errors.log）")
        return

    if all(r.ok for r in results):
        # 全部走文件消息成功：只回一条简短清单，不必再发卡片
        await event.reply(_summary(results))
        return

    # 有文件没发出去 → 正文改走**合并转发**（结果清单 + 各失败文件末尾若干行）
    nodes = build_forward_nodes(
        results,
        uin=getattr(event, "self_id", None) or getattr(module, "bot_id", None),
        tail_lines=int(_num_cfg(module, "fallback_forward_lines", DEFAULT_FORWARD_LINES)),
    )
    forwarded, forward_detail = await send_forward(event, nodes)
    if forwarded:
        logger.info(f"日志文件未全部发出，正文已合并转发（{forward_detail}）")
        return

    # 合并转发也不行 → 极端兜底：纯文本贴末尾几行
    logger.warning(f"日志合并转发也失败（{forward_detail}），降级为纯文本")
    await event.reply(_summary(results, int(_num_cfg(module, "fallback_text_lines", DEFAULT_FALLBACK_TEXT_LINES))))


def _summary(results: list[SendResult], preview_lines: int = 0) -> str:
    """汇总一条：发了哪些、各自多大、失败原因，失败时附原文末尾几行。"""
    sent = sum(1 for r in results if r.ok)
    head = f"本轮次日志已发送 {sent}/{len(results)} 个："
    rows: list[str] = []
    previews: list[str] = []
    for r in results:
        flag = "✅" if r.ok else "❌"
        note = "（已截断）" if r.snapshot.truncated else ""
        row = f"{flag} {r.snapshot.name} · {_human_size(r.snapshot.size)}{note}"
        if not r.ok:
            row += f" · {r.detail}"
        rows.append(row)
        if not r.ok:
            text = _preview_lines(r.snapshot, preview_lines)
            if text:
                previews.append(f"—— {r.snapshot.name} 末尾 {min(preview_lines, r.snapshot.lines)} 行 ——\n{text}")

    message = "\n".join([head, *rows])
    if previews:
        message += "\n\n" + "\n\n".join(previews)
    return message[:4000]
