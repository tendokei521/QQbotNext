"""协议端安装：下载 → 校验 sha256 → 落盘 → 切当前版本（可回滚上一版）。

布局（``<data_root>/protocol/``）::

    manifest.json            # 用户覆盖清单（可选）
    napcat/1.2.0/包内文件…
    napcat/current.json      # {"version": "1.2.0", "installed_at": …}
    napcat/previous.json     # 上一版（回滚用）

下载器可注入（``fetcher(url) -> bytes``），单测不联网。
"""

from __future__ import annotations

import hashlib
import io
import json
import time
import zipfile
from collections.abc import Callable
from pathlib import Path

from app.application.protocol.manifest import (
    ProtocolError,
    load_manifest,
    select_version,
)

#: 单个包体积上限（防把磁盘写满；协议端本体通常 < 200 MB）
MAX_PACKAGE_BYTES = 300 * 1024 * 1024
MAX_FILES = 5000


def _safe_parts(name: str) -> list[str]:
    text = str(name or "").replace("\\", "/")
    if not text or text.startswith("/") or ".." in text.split("/"):
        raise ProtocolError("PROTOCOL_PACKAGE_UNSAFE", f"安装包内含不安全路径：{name}")
    parts = [part for part in text.split("/") if part]
    if parts and len(parts[0]) == 2 and parts[0][1] == ":":
        raise ProtocolError("PROTOCOL_PACKAGE_UNSAFE", f"安装包内含盘符路径：{name}")
    return parts


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


class ProtocolInstaller:
    def __init__(self, data_root: Path | str, fetcher: Callable[[str], bytes] | None = None) -> None:
        self.data_root = Path(data_root)
        self._fetcher = fetcher

    # ==================== 路径 / 状态 ====================

    @property
    def protocol_root(self) -> Path:
        return self.data_root / "protocol"

    def _dir(self, protocol: str) -> Path:
        if not protocol or "/" in protocol or "\\" in protocol or ".." in protocol:
            raise ProtocolError("PROTOCOL_UNKNOWN", f"协议端名称不合法：{protocol}")
        return self.protocol_root / protocol

    def manifest(self) -> dict:
        return load_manifest(self.data_root)

    def status(self, protocol: str) -> dict:
        directory = self._dir(protocol)
        current = _read_json(directory / "current.json")
        previous = _read_json(directory / "previous.json")
        return {
            "protocol": protocol,
            "installed": bool(current.get("version")),
            "version": str(current.get("version") or ""),
            "installed_at": int(current.get("installed_at") or 0),
            "previous_version": str(previous.get("version") or ""),
            "path": str(directory / str(current.get("version") or "")),
        }

    def list_installed_versions(self, protocol: str) -> list[str]:
        directory = self._dir(protocol)
        if not directory.is_dir():
            return []
        return sorted(
            (
                item.name
                for item in directory.iterdir()
                if item.is_dir() and item.name not in ("__pycache__",)
            ),
            reverse=True,
        )

    # ==================== 安装 ====================

    def _download(self, url: str) -> bytes:
        if self._fetcher is not None:
            data = self._fetcher(url)
        else:  # pragma: no cover —— 真实下载路径由手动验证覆盖
            import urllib.request

            with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 —— 只接受清单里的 https 地址
                data = response.read(MAX_PACKAGE_BYTES + 1)
        if not isinstance(data, (bytes, bytearray)) or not data:
            raise ProtocolError("PROTOCOL_DOWNLOAD_FAILED", "下载结果为空")
        if len(data) > MAX_PACKAGE_BYTES:
            raise ProtocolError("PROTOCOL_DOWNLOAD_FAILED", "安装包超过 300 MB")
        return bytes(data)

    def install(self, protocol: str, version: str | None = None) -> dict:
        """安装清单内版本；校验通过才落盘，失败不留"半装"目录。"""
        target = select_version(self.manifest(), protocol, version)
        data = self._download(target["url"])

        digest = hashlib.sha256(data).hexdigest()
        if digest != target["sha256"]:
            raise ProtocolError(
                "PROTOCOL_CHECKSUM_MISMATCH",
                f"{protocol} {target['version']} 校验失败（sha256 不匹配）",
                "下载内容可能被篡改或清单写错；已拒绝安装",
            )
        if target["size"] and len(data) != target["size"]:
            raise ProtocolError(
                "PROTOCOL_CHECKSUM_MISMATCH",
                f"{protocol} {target['version']} 体积不符（期望 {target['size']}，实际 {len(data)}）",
            )

        directory = self._dir(protocol)
        directory.mkdir(parents=True, exist_ok=True)
        staging = directory / f".staging-{target['version']}"
        if staging.exists():
            for item in sorted(staging.rglob("*"), reverse=True):
                item.unlink() if item.is_file() else item.rmdir()
            staging.rmdir()

        try:
            self._extract(data, staging)
        except Exception:
            for item in sorted(staging.rglob("*"), reverse=True) if staging.exists() else []:
                item.unlink() if item.is_file() else item.rmdir()
            if staging.exists():
                staging.rmdir()
            raise

        final = directory / target["version"]
        if final.exists():
            for item in sorted(final.rglob("*"), reverse=True):
                item.unlink() if item.is_file() else item.rmdir()
            final.rmdir()
        staging.rename(final)

        previous = _read_json(directory / "current.json")
        if previous.get("version") and previous["version"] != target["version"]:
            (directory / "previous.json").write_text(
                json.dumps(previous, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        current = {
            "version": target["version"],
            "installed_at": int(time.time()),
            "sha256": digest,
            "path": str(final),
        }
        (directory / "current.json").write_text(
            json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {"protocol": protocol, **current}

    @staticmethod
    def _extract(data: bytes, target: Path) -> None:
        import os

        target.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                infos = [info for info in archive.infolist() if not info.is_dir()]
                if len(infos) > MAX_FILES:
                    raise ProtocolError("PROTOCOL_PACKAGE_UNSAFE", f"文件数超过 {MAX_FILES}")
                for info in infos:
                    parts = _safe_parts(info.filename)
                    if not parts:
                        continue
                    destination = target.joinpath(*parts)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info) as source, open(destination, "wb") as sink:
                        sink.write(source.read())
        except zipfile.BadZipFile as e:
            raise ProtocolError(
                "PROTOCOL_PACKAGE_INVALID", f"安装包不是有效 zip：{e}", "协议端发行包应为 zip"
            ) from e
        except ProtocolError:
            raise
        except OSError as e:  # pragma: no cover —— 磁盘错误
            raise ProtocolError("PROTOCOL_PACKAGE_INVALID", f"解压失败：{e}") from e
        _ = os  # 保留导入位，避免平台差异下的 lint 抖动

    # ==================== 回滚 ====================

    def rollback(self, protocol: str) -> dict:
        """切回上一版（不删除新版本目录，便于再次切回）。"""
        directory = self._dir(protocol)
        previous = _read_json(directory / "previous.json")
        if not previous.get("version"):
            raise ProtocolError("PROTOCOL_NO_ROLLBACK", f"{protocol} 没有可回滚的上一版")
        target_dir = directory / str(previous["version"])
        if not target_dir.is_dir():
            raise ProtocolError(
                "PROTOCOL_NO_ROLLBACK",
                f"{protocol} 上一版目录已不存在：{previous['version']}",
                "重新安装该版本后再回滚",
            )
        current = _read_json(directory / "current.json")
        current_path = directory / "current.json"
        current_path.write_text(json.dumps(previous, ensure_ascii=False, indent=2), encoding="utf-8")
        if current.get("version"):
            (directory / "previous.json").write_text(
                json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        return {"protocol": protocol, **previous}

    def uninstall(self, protocol: str) -> dict:
        """删除所有已装版本与指针（不动用户清单）。"""
        directory = self._dir(protocol)
        if not directory.is_dir():
            return {"protocol": protocol, "removed": []}
        removed: list[str] = []
        for item in sorted(directory.iterdir()):
            if item.is_file():
                item.unlink()
                removed.append(item.name)
                continue
            for child in sorted(item.rglob("*"), reverse=True):
                child.unlink() if child.is_file() else child.rmdir()
            item.rmdir()
            removed.append(item.name + "/")
        return {"protocol": protocol, "removed": removed}
