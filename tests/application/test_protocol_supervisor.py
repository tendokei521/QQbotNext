"""P5-2：协议端进程托管（启动/停止/重启/状态/日志尾/崩溃拉起/退出清理）。

用假进程验证：不真的起 NapCat，但把"面板要回答的问题"全部锁定。
"""

from __future__ import annotations

import json
from pathlib import Path

from app.application.protocol.supervisor import (
    MAX_RESTARTS,
    ProtocolProcessError,
    ProtocolSupervisor,
)


class FakeProc:
    def __init__(self, code=None):
        self.pid = 4242
        self._code = code
        self.terminated = False
        self.killed = False

    def poll(self):
        return self._code

    def terminate(self):
        self.terminated = True
        self._code = 0

    def kill(self):
        self.killed = True
        self._code = -9

    def wait(self, timeout=None):
        return self._code


def _installed(tmp_path: Path, version="1.2.0", launcher="napcat.exe") -> Path:
    root = tmp_path / "protocol"
    target = root / "napcat" / version
    target.mkdir(parents=True)
    (target / launcher).write_text("bin", encoding="utf-8")
    (root / "napcat" / "current.json").write_text(
        json.dumps({"version": version, "installed_at": 1}), encoding="utf-8"
    )
    return root


def _supervisor(tmp_path, proc=None):
    holder = {"proc": proc or FakeProc(), "spawned": []}

    def spawn(command, cwd=None, stdout=None, stderr=None):
        holder["spawned"].append({"command": command, "cwd": cwd})
        return holder["proc"]

    clock = {"now": 1000.0}
    supervisor = ProtocolSupervisor(
        _installed(tmp_path), spawn=spawn, clock=lambda: clock["now"]
    )
    return supervisor, holder, clock


def test_status_before_install(tmp_path):
    supervisor = ProtocolSupervisor(tmp_path / "protocol")
    status = supervisor.status("napcat")
    assert status["installed"] is False
    assert status["running"] is False


def test_start_and_status(tmp_path):
    supervisor, holder, clock = _supervisor(tmp_path)

    status = supervisor.start("napcat")

    assert status["running"] is True
    assert status["pid"] == 4242
    assert status["version"] == "1.2.0"
    assert holder["spawned"][0]["command"][0].endswith("napcat.exe")
    clock["now"] += 30
    assert supervisor.status("napcat")["uptime_s"] == 30


def test_start_twice_is_idempotent(tmp_path):
    supervisor, holder, _ = _supervisor(tmp_path)
    supervisor.start("napcat")
    supervisor.start("napcat")
    assert len(holder["spawned"]) == 1


def test_stop_terminates_and_clears_state(tmp_path):
    supervisor, holder, _ = _supervisor(tmp_path)
    supervisor.start("napcat")

    status = supervisor.stop("napcat")

    assert holder["proc"].terminated is True
    assert status["running"] is False
    assert status["last_exit"]["reason"] == "stopped_by_user"


def test_restart_spawns_new_process(tmp_path):
    supervisor, holder, _ = _supervisor(tmp_path)
    supervisor.start("napcat")
    holder["proc"] = FakeProc()

    status = supervisor.restart("napcat")

    assert status["running"] is True
    assert len(holder["spawned"]) == 2


def test_start_without_install_fails_clearly(tmp_path):
    supervisor = ProtocolSupervisor(tmp_path / "protocol")
    try:
        supervisor.start("napcat")
        raise AssertionError("未安装就启动应报错")
    except ProtocolProcessError as e:
        assert e.code == "PROTOCOL_NOT_INSTALLED"


def test_missing_launcher_reports_clearly(tmp_path):
    root = _installed(tmp_path, launcher="something-else.txt")
    supervisor = ProtocolSupervisor(root, spawn=lambda *a, **k: FakeProc())
    try:
        supervisor.start("napcat")
        raise AssertionError("找不到启动文件应报错")
    except ProtocolProcessError as e:
        assert e.code == "PROTOCOL_LAUNCHER_MISSING"


def test_health_check_records_crash_and_restarts(tmp_path):
    supervisor, holder, clock = _supervisor(tmp_path)
    supervisor.start("napcat")
    holder["proc"]._code = 1          # 进程崩了
    holder["proc"] = FakeProc()       # 下一次 spawn 的新进程

    status = supervisor.health_check("napcat")

    assert status["running"] is True
    assert status["restarts"] == 1
    assert status["last_exit"]["reason"] == "crashed"
    assert status["last_exit"]["code"] == 1


def test_health_check_stops_after_max_restarts(tmp_path):
    supervisor, holder, clock = _supervisor(tmp_path)
    supervisor.start("napcat")
    status = {}
    for _ in range(MAX_RESTARTS + 1):
        holder["proc"]._code = 1
        holder["proc"] = FakeProc()
        status = supervisor.health_check("napcat")
        clock["now"] += 1

    assert status["restart_suppressed"] is True
    assert "已停止自动拉起" in status["hint"]


def test_health_check_restart_window_resets(tmp_path):
    supervisor, holder, clock = _supervisor(tmp_path)
    supervisor.start("napcat")
    holder["proc"]._code = 1
    holder["proc"] = FakeProc()
    supervisor.health_check("napcat")
    # 窗口过后再崩一次，允许继续拉起
    clock["now"] += 600
    holder["proc"]._code = 1
    holder["proc"] = FakeProc()
    status = supervisor.health_check("napcat")
    assert not status.get("restart_suppressed")


def test_stop_all_collects_children(tmp_path):
    supervisor, holder, _ = _supervisor(tmp_path)
    supervisor.start("napcat")

    stopped = supervisor.stop_all()

    assert stopped == ["napcat"]
    assert supervisor.status("napcat")["running"] is False


def test_tail_returns_last_lines(tmp_path):
    root = _installed(tmp_path)
    (root / "napcat" / "protocol.log").write_text(
        "\n".join(f"line-{index}" for index in range(200)), encoding="utf-8"
    )
    supervisor = ProtocolSupervisor(root, spawn=lambda *a, **k: FakeProc())

    tail = supervisor.tail("napcat", lines=3)

    assert tail.splitlines() == ["line-197", "line-198", "line-199"]
