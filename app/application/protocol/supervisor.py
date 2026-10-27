"""协议端进程托管（计划 P5-2）：启动 / 停止 / 重启 / 状态 / 日志尾 / 崩溃拉起。

为什么值得单独一层：协议端（NapCat 等）是**外部进程**，用户最常见的求助是
"我点了启动，但机器人连不上"——如果面板不能回答"它到底起没起、为什么退、日志最后几行"，
这个问题就没法自助解决。

三条设计约束：
1. **进程与框架同生共死**：框架退出时 ``stop_all()`` 必须把子进程收掉，不留孤儿；
2. **崩溃可观测**：退出码/时间/最后一段 stderr 进 ``status``，并有次数上限的自动拉起，
   避免"启动即崩溃 → 无限重启"把机器拖死；
3. **可注入**：单测用假的 ``spawn``，不真的起进程。
"""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

#: 崩溃自动拉起的窗口与上限（防止无限重启）
RESTART_WINDOW_S = 300
MAX_RESTARTS = 3
#: 停止等待时间（秒）：先 terminate，再 kill
STOP_TIMEOUT_S = 8


class ProtocolProcessError(RuntimeError):
    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.hint = hint


class ProtocolSupervisor:
    def __init__(
        self,
        protocol_root: Path | str,
        *,
        spawn: Callable[..., Any] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.protocol_root = Path(protocol_root)
        self._spawn = spawn
        self._clock = clock
        # protocol -> {"proc": handle, "started_at": float, "restarts": [float], "last_exit": dict}
        self._state: dict[str, dict] = {}

    # ==================== 路径 ====================

    def _dir(self, protocol: str) -> Path:
        if not protocol or any(ch in protocol for ch in ("/", "\\")) or ".." in protocol:
            raise ProtocolProcessError("PROTOCOL_UNKNOWN", f"协议端名称不合法：{protocol}")
        return self.protocol_root / protocol

    def _current_version(self, protocol: str) -> str:
        try:
            import json

            current = json.loads((self._dir(protocol) / "current.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ""
        return str(current.get("version") or "")

    def _log_path(self, protocol: str) -> Path:
        return self._dir(protocol) / "protocol.log"

    # ==================== 状态 ====================

    def _alive(self, handle: Any) -> bool:
        return handle is not None and handle.poll() is None

    def status(self, protocol: str) -> dict:
        state = self._state.get(protocol) or {}
        handle = state.get("proc")
        running = self._alive(handle)
        version = self._current_version(protocol)
        return {
            "protocol": protocol,
            "installed": bool(version),
            "version": version,
            "running": running,
            "pid": getattr(handle, "pid", None) if running else None,
            "started_at": int(state.get("started_at") or 0) if running else 0,
            "uptime_s": int(self._clock() - state["started_at"]) if running and state.get("started_at") else 0,
            "restarts": len(state.get("restarts") or []),
            "last_exit": state.get("last_exit"),
            "log_path": str(self._log_path(protocol)),
        }

    def tail(self, protocol: str, lines: int = 80) -> str:
        path = self._log_path(protocol)
        if not path.is_file():
            return ""
        try:
            content = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ""
        return "\n".join(content[-max(1, int(lines)):])

    # ==================== 启动 / 停止 ====================

    def _launch(self, protocol: str, args: list[str] | None) -> Any:
        version = self._current_version(protocol)
        if not version:
            raise ProtocolProcessError(
                "PROTOCOL_NOT_INSTALLED", f"{protocol} 还没有安装任何版本",
                "先在协议端面板安装一个固定版本",
            )
        cwd = self._dir(protocol) / version
        if not cwd.is_dir():
            raise ProtocolProcessError(
                "PROTOCOL_NOT_INSTALLED", f"{protocol} {version} 的目录不存在，请重新安装"
            )
        command = [str(item if os.path.isabs(item) else cwd / item) for item in (args or [])]
        if not command:
            command = self._default_command(cwd, protocol)
        self._log_path(protocol).parent.mkdir(parents=True, exist_ok=True)
        log_file = open(self._log_path(protocol), "ab")  # noqa: SIM115 —— 交给子进程持有，进程结束由 Popen 关闭
        try:
            handle = (self._spawn or subprocess.Popen)(
                command, cwd=str(cwd), stdout=log_file, stderr=subprocess.STDOUT,
            )
        except OSError as e:
            log_file.close()
            raise ProtocolProcessError("PROTOCOL_SPAWN_FAILED", f"{protocol} 启动失败：{e}") from e
        return handle

    @staticmethod
    def _default_command(cwd: Path, protocol: str) -> list[str]:
        """按平台探测可执行文件；找不到就明确报错（不猜）。"""
        candidates = {
            "napcat": ["napcat.exe", "napcat", "NapCatWinBootMain.exe", "launcher.bat", "launcher.sh"],
        }.get(protocol, ["launcher.bat", "launcher.sh", protocol, f"{protocol}.exe"])
        for name in candidates:
            path = cwd / name
            if path.is_file():
                return [str(path)]
        raise ProtocolProcessError(
            "PROTOCOL_LAUNCHER_MISSING",
            f"在 {cwd.name} 里找不到 {protocol} 的启动文件",
            "安装包可能不完整，重新安装该版本",
        )

    def start(self, protocol: str, args: list[str] | None = None) -> dict:
        state = self._state.setdefault(protocol, {"proc": None, "started_at": 0.0,
                                                  "restarts": [], "last_exit": None})
        if self._alive(state.get("proc")):
            return self.status(protocol)
        state["proc"] = self._launch(protocol, args)
        state["started_at"] = self._clock()
        return self.status(protocol)

    def stop(self, protocol: str, timeout: float = STOP_TIMEOUT_S) -> dict:
        state = self._state.get(protocol) or {}
        handle = state.get("proc")
        if not self._alive(handle):
            return self.status(protocol)
        handle.terminate()
        deadline = self._clock() + timeout
        while self._clock() < deadline and handle.poll() is None:
            time.sleep(0.05)
        if handle.poll() is None:
            handle.kill()
            try:
                handle.wait(timeout=timeout)
            except Exception as e:  # noqa: BLE001 —— 停止失败要留痕但不能卡住退出
                state["last_exit"] = {"error": str(e)}
        state["last_exit"] = {"code": handle.poll(), "at": int(self._clock()), "reason": "stopped_by_user"}
        state["proc"] = None
        state["started_at"] = 0.0
        return self.status(protocol)

    def restart(self, protocol: str, args: list[str] | None = None) -> dict:
        self.stop(protocol)
        return self.start(protocol, args)

    # ==================== 崩溃拉起 ====================

    def health_check(self, protocol: str, args: list[str] | None = None) -> dict:
        """由定时器调用：进程没了就记录退出原因，并按上限自动拉起。"""
        state = self._state.get(protocol)
        if not state or not state.get("proc"):
            return self.status(protocol)
        handle = state["proc"]
        code = handle.poll()
        if code is None:
            return self.status(protocol)

        now = self._clock()
        state["last_exit"] = {"code": code, "at": int(now), "reason": "crashed"}
        state["proc"] = None
        state["started_at"] = 0.0
        recent = [item for item in (state.get("restarts") or []) if now - item <= RESTART_WINDOW_S]
        if len(recent) >= MAX_RESTARTS:
            state["restarts"] = recent
            return {**self.status(protocol), "restart_suppressed": True,
                    "hint": f"{RESTART_WINDOW_S}s 内已重启 {MAX_RESTARTS} 次，已停止自动拉起，请看日志"}
        recent.append(now)
        state["restarts"] = recent
        try:
            self.start(protocol, args)
        except ProtocolProcessError as e:
            return {**self.status(protocol), "restart_error": str(e), "hint": e.hint}
        return self.status(protocol)

    # ==================== 退出清理 ====================

    def stop_all(self) -> list[str]:
        """框架退出时调用：把所有子进程收掉，避免留孤儿进程。"""
        stopped: list[str] = []
        for protocol in list(self._state):
            try:
                if self._alive((self._state[protocol] or {}).get("proc")):
                    self.stop(protocol)
                    stopped.append(protocol)
            except Exception:  # noqa: BLE001 —— 退出路径上不允许抛错
                continue
        return stopped
