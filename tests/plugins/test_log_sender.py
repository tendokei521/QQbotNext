"""日志发送插件测试。

覆盖：命令声明（框架统一命令系统）/ 本轮次文件挑选 / 尾部裁剪（行·字节）/
群·私聊上传通道 / 上传失败降级 file 段 / 彻底失败回贴原文 / 关闭开关。
"""

from types import SimpleNamespace

from app.plugin import loader
from app.plugin.base import ModuleAuthority, ModuleConfig, ModuleContext, ServiceAccess
from app.plugins.log_sender import service
from app.plugins.log_sender.module import Module

DEFAULTS = {
    "enable": True,
    "tail_lines": 2000,
    "max_file_mb": 8,
    "fallback_text_lines": 30,
}


class FakeConfig:
    def __init__(self, data: dict) -> None:
        self.data = data

    def get(self, key: str, default=None):
        return self.data.get(key, default)


class FakeBot:
    def __init__(self, ok: bool = True, forward_ok: bool = True) -> None:
        self.ok = ok
        self.forward_ok = forward_ok
        self.calls: list[tuple[str, dict]] = []
        self.forwards: list[dict] = []

    async def call_api(self, action: str, params: dict | None = None):
        self.calls.append((action, dict(params or {})))
        if self.ok:
            return {"status": "ok", "retcode": 0}
        return {"status": "failed", "retcode": 1200, "message": "不支持"}

    async def send_forward_msg(self, group_id: int = 0, user_id: int = 0, msgdata=None, **kw):
        self.forwards.append({"group_id": group_id, "user_id": user_id, "nodes": msgdata})
        if self.forward_ok:
            return {"status": "ok", "retcode": 0, "data": {"message_id": 9}}
        return {"status": "failed", "retcode": 1200}


class FakeEvent:
    def __init__(
        self,
        text: str,
        *,
        bot=None,
        message_type: str = "group",
        group_id: int | None = 10086,
        user_id: int = 10001,
        reply_ok: bool = True,
        args: list[str] | None = None,
    ) -> None:
        self.text = text
        self.bot = bot
        self.message_type = message_type
        self.group = SimpleNamespace(group_id=group_id) if group_id else None
        self.group_id = group_id
        self.user_id = user_id
        self.reply_ok = reply_ok
        self.replies: list = []
        # 命令节点会填这个；直接调 service 时手造一份等价物
        self.command = SimpleNamespace(
            name=text.strip().lstrip("/"), args=list(args or []), text="", prefix="/", module="log_sender"
        )

    async def reply(self, message, **kwargs):
        self.replies.append(message)
        return {"status": "ok"} if self.reply_ok else {"status": "failed", "retcode": 1404}


class _Svc:
    def get_module_config(self, *a):
        return {}

    def get_module_authority(self, *a):
        return {}


def _make_module(tmp_path, **config):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    settings = SimpleNamespace(log_dir=log_dir, module_data_dir=tmp_path / "data")
    s = _Svc()
    ctx = ModuleContext(
        module_name="log_sender",
        bot_id=1,
        config=ModuleConfig("log_sender", 1, {}, s),
        authority=ModuleAuthority("log_sender", 1, s),
        services=ServiceAccess(settings=settings),
    )
    module = Module(ctx)
    module.module_name = "log_sender"
    module.config = FakeConfig({**DEFAULTS, **config})  # 便于逐条用例改配置
    loader.bind_commands(module, Module)
    return module, log_dir


def _write_logs(log_dir, names=("debug.log", "warn.log", "errors.log", "user.log")):
    for name in names:
        (log_dir / name).write_text(f"{name} 内容\n", encoding="utf-8")


# ==================== 命令声明 ====================


def test_module_declares_log_and_logs_commands(tmp_path):
    module, _log_dir = _make_module(tmp_path)
    specs = {spec.name: spec for spec, _handler in module._commands}

    assert set(specs) == {"log", "logs"}
    # 隐藏参数：两条命令都默认截断 LLM
    assert specs["log"].skip_llm is True
    assert specs["logs"].skip_llm is True


# ==================== 取文件 / 副本裁剪 ====================


def test_select_log_files_skips_missing(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "user.log").write_text("u\n", encoding="utf-8")
    (log_dir / "debug.log").write_text("d\n", encoding="utf-8")

    assert [p.name for p in service.select_log_files(log_dir, "log")] == ["user.log"]
    assert [p.name for p in service.select_log_files(log_dir, "logs")] == ["debug.log", "user.log"]


def test_build_copy_tail_lines_keeps_tail_and_marks_header(tmp_path):
    src = tmp_path / "debug.log"
    src.write_text("\n".join(f"第{i}行" for i in range(100)) + "\n", encoding="utf-8")

    snapshot = service.build_copy(src, tmp_path / "out", tail_lines=10)

    assert snapshot.name == "debug.txt"
    assert snapshot.truncated is True
    assert snapshot.lines == 10
    text = snapshot.path.read_text(encoding="utf-8")
    assert text.startswith("# debug.log 已截断")
    assert "第99行" in text
    assert "第89行" not in text


def test_build_copy_max_bytes_keeps_tail(tmp_path):
    src = tmp_path / "user.log"
    src.write_text("".join(f"{i:04d}\n" for i in range(1000)), encoding="utf-8")

    snapshot = service.build_copy(src, tmp_path / "out", tail_lines=0, max_bytes=100)

    assert snapshot.truncated is True
    text = snapshot.path.read_text(encoding="utf-8")
    assert "0999" in text
    assert "0000" not in text


def test_build_copy_full_file_has_no_header(tmp_path):
    src = tmp_path / "user.log"
    src.write_text("只有一行\n", encoding="utf-8")

    snapshot = service.build_copy(src, tmp_path / "out", tail_lines=2000)

    assert snapshot.truncated is False
    assert snapshot.path.read_text(encoding="utf-8") == "只有一行\n"


# ==================== 发送 ====================


async def test_send_log_uploads_user_txt(tmp_path):
    module, log_dir = _make_module(tmp_path)
    _write_logs(log_dir)
    bot = FakeBot()
    event = FakeEvent("/log", bot=bot)

    await service.send_logs(module, event, command="log")

    assert len(bot.calls) == 1
    action, params = bot.calls[0]
    assert action == "upload_group_file"
    assert params["group_id"] == 10086
    assert params["name"] == "user.txt"
    assert params["file"].endswith("user.txt")
    summary = event.replies[-1]
    assert "1/1" in summary and "user.txt" in summary


async def test_send_logs_uploads_all_four_files(tmp_path):
    module, log_dir = _make_module(tmp_path)
    _write_logs(log_dir)
    bot = FakeBot()
    event = FakeEvent("/logs", bot=bot)

    await service.send_logs(module, event, command="logs")

    names = [params["name"] for _action, params in bot.calls]
    assert names == ["debug.txt", "warn.txt", "errors.txt", "user.txt"]
    assert "4/4" in event.replies[-1]


async def test_send_private_uses_upload_private_file(tmp_path):
    module, log_dir = _make_module(tmp_path)
    _write_logs(log_dir, names=("user.log",))
    bot = FakeBot()
    event = FakeEvent("/log", bot=bot, message_type="private", group_id=None)

    await service.send_logs(module, event, command="log")

    action, params = bot.calls[0]
    assert action == "upload_private_file"
    assert params["user_id"] == 10001


async def test_send_falls_back_to_file_segment(tmp_path):
    module, log_dir = _make_module(tmp_path)
    _write_logs(log_dir)
    bot = FakeBot(ok=False)
    event = FakeEvent("/log", bot=bot)

    await service.send_logs(module, event, command="log")

    assert bot.calls[0][0] == "upload_group_file"
    first = event.replies[0]
    assert isinstance(first, list) and first[0]["type"] == "file"
    assert first[0]["data"]["name"] == "user.txt"
    assert first[0]["data"]["file"].startswith("file:///")
    assert "1/1" in event.replies[-1]


async def test_send_total_failure_uses_merged_forward(tmp_path):
    """文件消息发不出去（上传与 file 段都失败）→ 正文改走合并转发。"""
    module, log_dir = _make_module(tmp_path)
    (log_dir / "user.log").write_text("唯一一行日志\n", encoding="utf-8")
    bot = FakeBot(ok=False)
    event = FakeEvent("/log", bot=bot, reply_ok=False)  # file 段也发不出去

    await service.send_logs(module, event, command="log")

    assert len(bot.forwards) == 1
    nodes = bot.forwards[0]["nodes"]
    assert nodes[0]["type"] == "node"
    assert nodes[0]["data"]["uin"] == "1"  # 用 bot 自己的 uin
    assert "0/1" in nodes[0]["data"]["content"][0]["data"]["text"]  # 结果清单节点
    assert nodes[1]["data"]["name"] == "user.txt"
    assert "唯一一行日志" in nodes[1]["data"]["content"][0]["data"]["text"]
    # 合并转发成功时不再补发纯文本
    assert not any(isinstance(r, str) for r in event.replies)


async def test_send_all_failed_forwards_every_file(tmp_path):
    module, log_dir = _make_module(tmp_path)
    _write_logs(log_dir)
    bot = FakeBot(ok=False)
    event = FakeEvent("/logs", bot=bot, reply_ok=False)

    await service.send_logs(module, event, command="logs")

    names = [n["data"]["name"] for n in bot.forwards[0]["nodes"][1:]]
    assert names == ["debug.txt", "warn.txt", "errors.txt", "user.txt"]


async def test_send_forward_also_fails_falls_back_to_plain_text(tmp_path):
    """合并转发也失败 → 极端兜底：纯文本里贴末尾几行。"""
    module, log_dir = _make_module(tmp_path)
    (log_dir / "user.log").write_text("唯一一行日志\n", encoding="utf-8")
    bot = FakeBot(ok=False, forward_ok=False)
    event = FakeEvent("/log", bot=bot, reply_ok=False)

    await service.send_logs(module, event, command="log")

    assert len(bot.forwards) == 1
    summary = event.replies[-1]
    assert "0/1" in summary
    assert "唯一一行日志" in summary


async def test_forward_nodes_chunked_by_lines(tmp_path):
    module, log_dir = _make_module(tmp_path)
    (log_dir / "user.log").write_text(
        "\n".join(f"第{i}行" + "x" * 50 for i in range(200)) + "\n", encoding="utf-8"
    )
    bot = FakeBot(ok=False)
    event = FakeEvent("/log", bot=bot, reply_ok=False)

    await service.send_logs(module, event, command="log")

    body = bot.forwards[0]["nodes"][1:]
    assert len(body) > 1  # 正文超长 → 按行拆成多个节点
    assert body[0]["data"]["name"].startswith("user.txt (1/")
    assert body[-1]["data"]["name"].startswith(f"user.txt ({len(body)}/")


async def test_send_marks_truncated_file_in_summary(tmp_path):
    module, log_dir = _make_module(tmp_path, tail_lines=5)
    (log_dir / "user.log").write_text("\n".join(f"行{i}" for i in range(50)) + "\n", encoding="utf-8")
    bot = FakeBot()
    event = FakeEvent("/log", bot=bot)

    await service.send_logs(module, event, command="log")

    assert "已截断" in event.replies[-1]


async def test_send_disabled_does_nothing(tmp_path):
    module, log_dir = _make_module(tmp_path, enable=False)
    _write_logs(log_dir)
    bot = FakeBot()
    event = FakeEvent("/log", bot=bot)

    await service.send_logs(module, event, command="log")

    assert bot.calls == []
    assert event.replies == []


async def test_send_no_log_files_replies_warning(tmp_path):
    module, _log_dir = _make_module(tmp_path)
    bot = FakeBot()
    event = FakeEvent("/logs", bot=bot)

    await service.send_logs(module, event, command="logs")

    assert bot.calls == []
    assert "还没有日志文件" in event.replies[-1]
