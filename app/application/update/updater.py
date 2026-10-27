"""更新的暂存 / 备份 / 切换 / 回滚。

状态都落在 ``data/updates/<version>/``：

    stage/        解压后的新版本（切换前不动线上）
    backup/       更新前的 data 关键文件（app.db / .env）
    previous/     被替换掉的旧文件（回滚用）
    state.json    {phase: staged|applied|rolled_back, version, at}

``phase`` 是唯一真相：启动时若看到 ``applied`` 但版本号没变（说明切换中途崩了），
调用方可以据此提示或回滚。
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import time
import zipfile
from collections.abc import Callable
from pathlib import Path

from app.application.update.version import compare_versions, read_local_version, select_kit_asset

#: 更新包里允许被覆盖的路径前缀（只动程序，不动 data/logs/venv）
UPDATABLE_PREFIXES = ("app/", "scripts/", "docs/")
UPDATABLE_FILES = ("main.py", "start.py", "check_deps.py", "pyproject.toml", "README.md")
#: 更新前必须备份的 data 文件（相对 data_root）
BACKUP_PATTERNS = ("app.db", "app.db-wal", "app.db-shm")
MAX_KIT_BYTES = 300 * 1024 * 1024


class UpdateError(ValueError):
    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.hint = hint


class UpdateService:
    def __init__(
        self,
        root: Path | str,
        data_root: Path | str,
        *,
        fetcher: Callable[[str], bytes] | None = None,
        version: str | None = None,
    ) -> None:
        self.root = Path(root)
        self.data_root = Path(data_root)
        self._fetcher = fetcher
        self._version = version

    # ==================== 基础 ====================

    @property
    def local_version(self) -> str:
        return self._version or read_local_version(self.root)

    @property
    def updates_root(self) -> Path:
        return self.data_root / "updates"

    def _dir(self, version: str) -> Path:
        safe = "".join(ch for ch in str(version) if ch.isalnum() or ch in "._-") or "unknown"
        return self.updates_root / safe

    def _state_path(self, version: str) -> Path:
        return self._dir(version) / "state.json"

    def _read_state(self, version: str) -> dict:
        try:
            return json.loads(self._state_path(version).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _write_state(self, version: str, **fields) -> dict:
        state = {**self._read_state(version), **fields, "version": version, "at": int(time.time())}
        path = self._state_path(version)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        return state

    def status(self) -> dict:
        """当前版本 + 是否有待处理/可回滚的更新。"""
        staged: list[str] = []
        applied: list[str] = []
        if self.updates_root.is_dir():
            for entry in sorted(self.updates_root.iterdir()):
                if not entry.is_dir():
                    continue
                state = self._read_state(entry.name)
                if state.get("phase") == "staged":
                    staged.append(entry.name)
                elif state.get("phase") == "applied":
                    applied.append(entry.name)
        return {
            "current": self.local_version,
            "staged": staged,
            "applied": applied,
            "rollback_available": bool(applied),
        }

    # ==================== 检查 ====================

    def check(self, release: dict | None = None) -> dict:
        """对比远端 release（GitHub API 形状）与本地版本。``release=None`` 表示不联网。"""
        current = self.local_version
        if not release:
            return {"current": current, "latest": current, "update_available": False, "asset": None}
        tag = str(release.get("tag_name") or release.get("version") or "").strip()
        latest = tag.lstrip("v")
        try:
            newer = compare_versions(latest, current) > 0
        except ValueError:
            raise UpdateError("UPDATE_BAD_REMOTE", f"远端版本号无法解析：{tag!r}") from None
        return {
            "current": current,
            "latest": latest,
            "update_available": newer,
            "notes": str(release.get("body") or "")[:2000],
            "asset": select_kit_asset(release.get("assets") or []),
        }

    # ==================== 暂存 ====================

    def _download(self, url: str) -> bytes:
        if self._fetcher is not None:
            data = self._fetcher(url)
        else:  # pragma: no cover —— 真实下载由手动验证覆盖
            import urllib.request

            with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310 —— 只接受发行包地址
                data = response.read(MAX_KIT_BYTES + 1)
        if not isinstance(data, (bytes, bytearray)) or not data:
            raise UpdateError("UPDATE_DOWNLOAD_FAILED", "下载内容为空")
        if len(data) > MAX_KIT_BYTES:
            raise UpdateError("UPDATE_DOWNLOAD_FAILED", "发行包超过 300 MB")
        return bytes(data)

    def stage(self, asset: dict) -> dict:
        """下载并解压到暂存区（校验 sha256/体积），不动线上文件。"""
        url = str(asset.get("browser_download_url") or asset.get("url") or "").strip()
        if not url:
            raise UpdateError("UPDATE_NO_ASSET", "发行包缺少下载地址")
        data = self._download(url)
        expected = str(asset.get("sha256") or "").strip().lower()
        digest = hashlib.sha256(data).hexdigest()
        if expected and digest != expected:
            raise UpdateError("UPDATE_CHECKSUM_MISMATCH", "发行包 sha256 不匹配，已拒绝更新")
        size = asset.get("size")
        if isinstance(size, int) and size > 0 and size != len(data):
            raise UpdateError("UPDATE_CHECKSUM_MISMATCH", f"发行包体积不符（{len(data)} != {size}）")

        version = str(asset.get("version") or "").strip() or self._version_from_kit(data)
        if not version:
            raise UpdateError("UPDATE_BAD_KIT", "无法确定发行包版本（缺少 deploy.json）")
        if compare_versions(version, self.local_version) <= 0:
            raise UpdateError(
                "UPDATE_NOT_NEWER", f"发行包版本 {version} 不高于当前 {self.local_version}"
            )

        directory = self._dir(version)
        stage_dir = directory / "stage"
        if stage_dir.exists():
            shutil.rmtree(stage_dir)
        stage_dir.mkdir(parents=True)

        try:
            self._extract(data, stage_dir)
        except Exception:
            shutil.rmtree(stage_dir, ignore_errors=True)
            raise

        payload_root = self._payload_root(stage_dir)
        if not (payload_root / "app").is_dir():
            shutil.rmtree(stage_dir, ignore_errors=True)
            raise UpdateError("UPDATE_BAD_KIT", "发行包里没有 app/ 目录，不是有效的程序包")

        self._write_state(version, phase="staged", sha256=digest, files=self._count_files(payload_root))
        return {"version": version, "stage": str(stage_dir), "files": self._count_files(payload_root)}

    @staticmethod
    def _extract(data: bytes, target: Path) -> None:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                for info in archive.infolist():
                    if info.is_dir():
                        continue
                    name = info.filename.replace("\\", "/")
                    if name.startswith("/") or ".." in name.split("/"):
                        raise UpdateError("UPDATE_BAD_KIT", f"发行包内含不安全路径：{name}")
                    destination = target.joinpath(*name.split("/"))
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info) as source, open(destination, "wb") as sink:
                        shutil.copyfileobj(source, sink)
        except zipfile.BadZipFile as e:
            raise UpdateError("UPDATE_BAD_KIT", f"发行包不是有效 zip：{e}") from e

    @staticmethod
    def _payload_root(stage_dir: Path) -> Path:
        """发行包可能带一层 ``QQBotNext/`` 顶层目录；这里剥掉。"""
        entries = [item for item in stage_dir.iterdir() if item.name != "__MACOSX"]
        if len(entries) == 1 and entries[0].is_dir() and (entries[0] / "app").is_dir():
            return entries[0]
        return stage_dir

    @staticmethod
    def _count_files(path: Path) -> int:
        return sum(1 for item in path.rglob("*") if item.is_file())

    @staticmethod
    def _version_from_kit(data: bytes) -> str:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                for name in archive.namelist():
                    if name.endswith("deploy.json"):
                        deploy = json.loads(archive.read(name))
                        return str(deploy.get("version") or "").strip()
        except (zipfile.BadZipFile, json.JSONDecodeError):
            return ""
        return ""

    # ==================== 备份 / 切换 / 回滚 ====================

    def _backup_data(self, version: str) -> list[str]:
        backup_dir = self._dir(version) / "backup"
        backup_dir.mkdir(parents=True, exist_ok=True)
        saved: list[str] = []
        for pattern in BACKUP_PATTERNS:
            source = self.data_root / pattern
            if source.is_file():
                shutil.copy2(source, backup_dir / pattern)
                saved.append(pattern)
        env_file = self.root / ".env"
        if env_file.is_file():
            shutil.copy2(env_file, backup_dir / ".env")
            saved.append(".env")
        return saved

    def _updatable(self, relative: Path) -> bool:
        posix = relative.as_posix()
        return posix.startswith(UPDATABLE_PREFIXES) or posix in UPDATABLE_FILES

    def apply(self, version: str | None = None) -> dict:
        """把暂存版本切上线：备份 → 旧文件留底 → 覆盖可更新路径。"""
        version = str(version or "").strip() or (self.status()["staged"] or [""])[0]
        if not version:
            raise UpdateError("UPDATE_NOTHING_STAGED", "没有待应用的更新")
        state = self._read_state(version)
        if state.get("phase") != "staged":
            raise UpdateError("UPDATE_NOTHING_STAGED", f"{version} 不在暂存状态")
        payload_root = self._payload_root(self._dir(version) / "stage")
        if not payload_root.is_dir():
            raise UpdateError("UPDATE_NOTHING_STAGED", "暂存目录已丢失，请重新下载")

        backup = self._backup_data(version)
        previous_dir = self._dir(version) / "previous"
        previous_dir.mkdir(parents=True, exist_ok=True)
        replaced: list[str] = []
        try:
            for source in sorted(payload_root.rglob("*")):
                if not source.is_file():
                    continue
                relative = source.relative_to(payload_root)
                if not self._updatable(relative):
                    continue
                target = self.root / relative
                if target.is_file():
                    keep = previous_dir / relative
                    keep.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, keep)
                    replaced.append(relative.as_posix())
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        except OSError as e:
            self._write_state(version, phase="failed", error=str(e))
            raise UpdateError("UPDATE_APPLY_FAILED", f"更新失败（可回滚）：{e}") from e

        self._write_state(
            version,
            phase="applied",
            previous_version=self.local_version,
            replaced=replaced,
            backup=backup,
        )
        return {"version": version, "replaced": len(replaced), "backup": backup}

    def rollback(self, version: str | None = None) -> dict:
        """从 ``previous/`` 恢复被替换的文件，并恢复备份的 data 关键文件。"""
        version = str(version or "").strip() or (self.status()["applied"] or [""])[0]
        if not version:
            raise UpdateError("UPDATE_NO_ROLLBACK", "没有可回滚的更新")
        state = self._read_state(version)
        if state.get("phase") != "applied":
            raise UpdateError("UPDATE_NO_ROLLBACK", f"{version} 不在已应用状态")
        previous_dir = self._dir(version) / "previous"
        if not previous_dir.is_dir():
            raise UpdateError("UPDATE_NO_ROLLBACK", "上一版文件副本已丢失")

        restored = 0
        for source in sorted(previous_dir.rglob("*")):
            if not source.is_file():
                continue
            relative = source.relative_to(previous_dir)
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            restored += 1

        restored_data: list[str] = []
        backup_dir = self._dir(version) / "backup"
        for pattern in BACKUP_PATTERNS:
            source = backup_dir / pattern
            if source.is_file():
                shutil.copy2(source, self.data_root / pattern)
                restored_data.append(pattern)
        env_backup = backup_dir / ".env"
        if env_backup.is_file():
            shutil.copy2(env_backup, self.root / ".env")
            restored_data.append(".env")

        self._write_state(version, phase="rolled_back")
        return {"version": version, "restored_files": restored, "restored_data": restored_data}
