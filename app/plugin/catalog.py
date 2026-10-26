"""统一模块编目：把「内置根」与「外部安装根」合成一个逻辑列表。

为什么需要它（docs/module-management-redesign.md §3.1 D2）：
- 原实现 `_iter_module_roots()` 被 `discover()` / `load_all()` 各写一套，两处口径迟早漂移；
- WebUI 只能看到"已加载实例"，看不到"目录存在但没加载/加载失败/被软卸载"的模块；
- 同名冲突靠"扫到谁算谁"静默决定，外部副本成了死文件还不告警。

编目只做**只读观察**：扫描目录、解析清单、判定状态；不创建/删除任何文件，
写操作（安装/删除）由 `installer.py` 负责。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from app.core.logger import logger
from app.plugin.manifest import ManifestError, load_manifest

ModuleSource = Literal["builtin", "installed"]
ModuleStatus = Literal["ok", "error", "conflict", "uninstalled", "manifest_missing"]


@dataclass(frozen=True)
class ModuleEntry:
    """一个模块在编目里的快照（按需重扫，不持有实例）。"""

    name: str
    source: ModuleSource
    path: Path
    manifest: dict
    version: str
    display_name: str
    status: ModuleStatus
    error: str
    has_page: bool
    builtin: bool
    installed_at: float | None


class ModuleCatalog:
    """两个模块根的只读编目（内置优先，同名冲突显式化）。"""

    def __init__(
        self,
        *,
        builtin_dir: Path | str,
        installed_dir: Path | str | None,
        install_service: Any = None,
        log: Any = None,
    ) -> None:
        self.builtin_dir = Path(builtin_dir)
        self.installed_dir = Path(installed_dir) if installed_dir is not None else None
        self.install_service = install_service
        self.log = log or logger
        self._entries: list[ModuleEntry] | None = None
        self._by_name: dict[str, ModuleEntry] = {}
        self._conflicts: list[dict] = []
        # 告警去重：扫描会被 load_all 按账号重复调用，但同一个问题只该刷一次日志
        self._warned: set[str] = set()

    # ---------- 扫描 ----------
    def refresh(self) -> None:
        """丢弃缓存，下一次 scan()/get() 会重新读盘。"""
        self._entries = None
        self._by_name = {}
        self._conflicts = []

    def scan(self) -> list[ModuleEntry]:
        """重扫两个根，返回合并后的逻辑列表（内置在前，外部同名条目被排除）。"""
        builtin_entries = self._scan_root(self.builtin_dir, "builtin")
        installed_entries = self._scan_root(self.installed_dir, "installed")

        entries: list[ModuleEntry] = []
        by_name: dict[str, ModuleEntry] = {}
        conflicts: list[dict] = []

        for entry in builtin_entries:
            by_name[entry.name] = entry
            entries.append(entry)

        for entry in installed_entries:
            builtin_entry = by_name.get(entry.name)
            if builtin_entry is None:
                by_name[entry.name] = entry
                entries.append(entry)
                continue
            # 同名冲突：内置优先但必须显式化，外部副本不进逻辑列表（文件保留，人工处理）
            message = (
                f"外部插件 {entry.path} 与内置模块同名，已忽略外部副本（内置优先）"
            )
            merged = replace(builtin_entry, status="conflict", error=message)
            by_name[entry.name] = merged
            entries[entries.index(builtin_entry)] = merged
            conflicts.append({
                "name": entry.name,
                "builtin_path": str(builtin_entry.path),
                "installed_path": str(entry.path),
                "message": message,
            })
            self._warn_once(f"conflict:{entry.name}", f"[Module] 编目冲突: {message}")

        self._entries = entries
        self._by_name = by_name
        self._conflicts = conflicts
        return list(entries)

    def get(self, name: str) -> ModuleEntry | None:
        if self._entries is None:
            self.scan()
        return self._by_name.get(str(name))

    def conflicts(self) -> list[dict]:
        if self._entries is None:
            self.scan()
        return [dict(item) for item in self._conflicts]

    # ---------- 路径解析（修 P0-2：外部模块自定义页 404） ----------
    def resolve_path(self, name: str, source: str | None = None) -> Path | None:
        """按来源解析模块目录；目录不存在返回 None。

        - `source` 指定时只查该根（`installed` 才能解析 zip 插件）；
        - `source=None` 时按"实际存在优先、内置优先"（沿用旧行为，避免历史部署换源）。
        """
        parts = self._split_name(name)
        if parts is None:
            return None
        normalized = _normalize_source(source)
        roots: list[Path | None]
        if normalized == "builtin":
            roots = [self.builtin_dir]
        elif normalized == "installed":
            roots = [self.installed_dir]
        else:
            roots = [self.builtin_dir, self.installed_dir]
        for root in roots:
            if root is None:
                continue
            candidate = root.joinpath(*parts)
            if candidate.is_dir():
                return candidate
        return None

    # ---------- 内部 ----------
    @staticmethod
    def _split_name(name: str) -> list[str] | None:
        """模块名 → 路径分段；非法/越界名字返回 None（编目不允许路径穿越）。"""
        text = str(name or "").strip()
        if not text or text.startswith(("_", ".")):
            return None
        if "/" in text or "\\" in text or ":" in text:
            return None
        parts = text.split(".")
        if any(not part or part in (".", "..") for part in parts):
            return None
        return parts

    def _scan_root(self, root: Path | None, source: ModuleSource) -> list[ModuleEntry]:
        if root is None or not root.is_dir():
            return []
        try:
            children = sorted(root.iterdir())
        except OSError as e:  # noqa: BLE001 —— 读不了目录就当作没有模块，启动不能挂
            self.log.warning(f"[Module] 无法读取模块目录 {root}: {e}")
            return []
        return [
            self._entry_for(child, source)
            for child in children
            if child.is_dir() and not child.name.startswith(("_", "."))
        ]

    def _entry_for(self, path: Path, source: ModuleSource) -> ModuleEntry:
        name = path.name
        manifest_path = path / "module.json"
        has_page = (path / "pages" / "index.html").is_file()
        installed_at = self._installed_at(path, manifest_path) if source == "installed" else None

        manifest_data: dict = {}
        display_name = name
        version = ""
        error = ""
        status: ModuleStatus = "ok"

        if manifest_path.is_file():
            try:
                manifest = load_manifest(path)
            except ManifestError as e:
                error = str(e) + (f"（{e.hint}）" if e.hint else "")
            else:
                manifest_data = dict(manifest.raw)
                display_name = manifest.display_name or name
                version = manifest.version
                if source == "installed" and manifest.name != name:
                    status = "error"
                    error = (
                        f"清单 name={manifest.name!r} 与目录名 {name!r} 不一致"
                        "（安装器要求二者相等，否则配置/数据会串台）"
                    )
        else:
            error = f"缺少 {manifest_path.name}"

        if status == "ok" and error:
            if source == "builtin":
                # 内置清单缺失/损坏只降级不阻断：核心模块仍要用类属性兜底加载
                status = "manifest_missing"
                version = version or "0.0.0"
                self._warn_once(
                    f"manifest:{name}",
                    f"[Module] 内置模块 {name} 的清单不可用，使用类属性兜底: {error}",
                )
            else:
                status = "error"

        if status == "manifest_missing" and not version:
            version = "0.0.0"

        if self.install_service is not None:
            try:
                if self.install_service.is_uninstalled(name):
                    status = "uninstalled"
            except Exception as e:  # noqa: BLE001 —— 状态文件坏了不该让编目整体失败
                self.log.debug(f"[Module] 读取软卸载状态失败（已忽略）: {e}")

        return ModuleEntry(
            name=name,
            source=source,
            path=path,
            manifest=manifest_data,
            version=version or "0.0.0",
            display_name=display_name,
            status=status,
            error=error,
            has_page=has_page,
            builtin=source == "builtin",
            installed_at=installed_at,
        )

    def _installed_at(self, path: Path, manifest_path: Path) -> float | None:
        """外部模块安装时间：优先清单 mtime（安装时落盘），回退目录 mtime。"""
        for candidate in (manifest_path, path):
            try:
                return candidate.stat().st_mtime
            except OSError:
                continue
        return None

    def _warn_once(self, key: str, message: str) -> None:
        if key in self._warned:
            return
        self._warned.add(key)
        self.log.warning(message)


def _normalize_source(source: str | None) -> ModuleSource | None:
    """兼容旧调用：local→builtin、zip→installed。"""
    if source in (None, ""):
        return None
    if source in ("builtin", "local"):
        return "builtin"
    if source in ("installed", "zip"):
        return "installed"
    return None
