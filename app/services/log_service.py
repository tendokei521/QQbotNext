"""日志读取服务：供 WebUI 展示最近日志。

自原 webui/app.py 的 get_recent_logs / _read_last_n_lines 移植。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional


class LogService:
    def __init__(self, log_dir: Path) -> None:
        self.log_dir = Path(log_dir)

    def _read_last_n_lines(self, file_path: str, n: int) -> List[str]:
        try:
            with open(file_path, "rb") as f:
                f.seek(0, 2)
                file_size = f.tell()
                if file_size == 0:
                    return []
                # 从末尾回读，窗口自适应扩大，直到取满 N 行或读完整文件
                window = min(file_size, max(65536, n * 256))
                while True:
                    start_pos = max(0, file_size - window)
                    f.seek(start_pos)
                    if start_pos > 0:
                        f.readline()  # 丢弃不完整首行
                    content = f.read().decode("utf-8", errors="ignore")
                    lines = content.split("\n")
                    if start_pos == 0 or len(lines) > n:
                        break
                    window *= 2
                return lines[-n * 3:]
        except OSError:
            return []

    def get_recent_logs(self, max_lines: int = 50, levels: Optional[List[str]] = None) -> List[dict]:
        from app.infrastructure.config.config_service import mask_ws_url

        log_file = self.log_dir / "debug.log"
        if not log_file.exists():
            return []
        if levels is None:
            levels = ["info", "warning", "error"]
        levels = [l.lower() for l in levels]

        logs: List[dict] = []
        lines = self._read_last_n_lines(str(log_file), max_lines)
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            parts = line.split(" - ", 2)
            if len(parts) >= 3:
                timestamp = parts[0].replace("[Service] ", "").strip()
                level = parts[1].strip().lower()
                message = mask_ws_url(parts[2].strip())  # 兜底打码（含历史日志里的 token）
                if level not in levels:
                    continue
                logs.insert(0, {"timestamp": timestamp, "level": level, "message": message})
            else:
                if "info" in levels:
                    logs.insert(0, {"timestamp": "", "level": "info", "message": mask_ws_url(line)})
            if len(logs) >= max_lines:
                break
        return logs
