"""插件软卸载状态服务。

“卸载”只写配置文件，不删除插件目录/配置/数据，避免误删无备份。
配置文件名：module/uninstalled_modules.json。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


class ModuleInstallService:
    """读写软卸载模块清单。"""

    def __init__(self, state_file: Path | str) -> None:
        self.state_file = Path(state_file)

    def load_state(self) -> dict[str, dict[str, Any]]:
        """读取软卸载模块映射；文件不存在或损坏时返回空 dict。"""
        if not self.state_file.exists():
            return {}
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        result: dict[str, dict[str, Any]] = {}
        for key, value in data.items():
            if isinstance(value, dict):
                result[str(key)] = dict(value)
        return result

    def is_uninstalled(self, module_name: str) -> bool:
        return str(module_name) in self.load_state()

    def list_uninstalled(self) -> list[dict[str, Any]]:
        """返回软卸载模块列表（含模块名），供 WebUI 展示。"""
        result = []
        for name, record in sorted(self.load_state().items()):
            result.append({
                "module_name": name,
                "source": record.get("source", "local"),
                "display_name": record.get("display_name", name),
                "version": record.get("version", ""),
                "uninstalled_at": record.get("uninstalled_at", 0),
            })
        return result

    def uninstall(
        self,
        module_name: str,
        *,
        source: str = "local",
        display_name: str = "",
        version: str = "",
    ) -> None:
        """记录软卸载状态。不删除任何文件。"""
        state = self.load_state()
        state[str(module_name)] = {
            "source": source,
            "display_name": display_name or str(module_name),
            "version": version,
            "uninstalled_at": int(time.time()),
        }
        self._write_state(state)

    def reinstall(self, module_name: str) -> None:
        """清除软卸载状态。"""
        state = self.load_state()
        state.pop(str(module_name), None)
        self._write_state(state)

    def _write_state(self, state: dict[str, dict[str, Any]]) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, self.state_file)
