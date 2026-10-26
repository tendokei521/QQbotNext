"""zip 插件安装器：布局容错 + 事务化落盘。

为什么需要单独一层（docs/module-management-redesign.md §3.1 D6/D7）：
- 原 `install_from_zip` 是"边解压边落地、装完再试加载"，任何一步失败都会在
  `data/module/plugins/<name>/` 留下半成品，下次安装还会被判"已安装"挡住重试；
- 只认"顶层目录名 == manifest.name"一种布局，GitHub 的 `repo-main/`、mac 的
  `__MACOSX/`、多层包装都会装成嵌套目录（插件根错位 → 加载失败）。

本模块的约定：**安装成功的判据是"目标目录下直接有 module.json + 入口文件"**，
而不是"zip 解压完了"。所有步骤在 staging 里完成，最后 `os.replace` 原子换入；
失败由调用方回滚备份（`rollback`），不留半成品。
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import stat
import time
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.logger import logger
from app.plugin.manifest import (
    DEFAULT_ENTRY,
    NAME_PATTERN,
    Manifest,
    ManifestError,
    load_manifest,
)

#: zip 安全阈值（对应 §3.1 D8）：超过即拒绝，避免 zip 炸弹拖死进程
MAX_ARCHIVE_FILES = 5000
MAX_ARCHIVE_BYTES = 200 * 1024 * 1024
MAX_ENTRY_DEPTH = 32
#: 覆盖安装备份保留份数（§3.2 目标目录树）
BACKUP_KEEP = 3
#: 归一插件根时要丢弃的噪声目录名（GitHub / macOS / IDE 产物）
NOISE_NAMES = frozenset({
    "__MACOSX", "__pycache__", ".git", ".github", ".idea", ".vscode",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".DS_Store", "Thumbs.db",
})
#: Windows 保留设备名不能当目录名
_WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
)
_DRIVE_PATTERN = re.compile(r"^[A-Za-z]:")


class ModuleInstallError(ValueError):
    """安装/删除失败。`hint` 是给用户的下一步建议，WebUI 直接展示。"""

    def __init__(self, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint


@dataclass(frozen=True)
class InstallResult:
    """一次成功安装的结果（回滚时还需要 backup/path）。"""

    name: str
    display_name: str
    version: str
    path: Path
    overwritten: bool
    backup: Path | None
    files: int
    manifest: dict


def normalize_plugin_root(raw_dir: Path) -> Path:
    """把解压目录归一成"插件根"：剥掉噪声目录与唯一包装目录，直到直接含清单/入口。

    支持（对应审计附录 A 的 A-D 常见布局）：
    - `zip 根` 直接是插件根；
    - `demo/`（唯一顶层目录）；
    - `repo-main/`（GitHub 下载，名字与插件名无关）；
    - `demo/` + `docs/`（多个顶层目录，但只有一个像插件）；
    - `repo-main/plugin/`（多层单链包装）。
    出现 0 个或 ≥2 个候选插件根则报错——不猜，避免装错。
    """
    current = Path(raw_dir)
    if not current.is_dir():
        raise ModuleInstallError(f"解压目录不存在: {current}", hint="压缩包可能为空或损坏")

    for _ in range(MAX_ENTRY_DEPTH):
        _prune_noise(current)
        if _looks_like_plugin(current):
            return current
        children = [
            child for child in sorted(current.iterdir())
            if child.is_dir() and not _is_noise(child.name)
        ]
        plugin_children = [child for child in children if _looks_like_plugin(child)]
        if len(plugin_children) == 1:
            current = plugin_children[0]
            continue
        if len(plugin_children) > 1:
            names = "、".join(child.name for child in plugin_children)
            raise ModuleInstallError(
                f"压缩包里有多个插件目录（{names}），无法确定装哪一个",
                hint="一个 zip 只能包含一个插件：请只打包目标插件目录（根下直接有 module.json + main.py）",
            )
        if len(children) == 1:
            # 单纯的包装目录（本层没有清单/入口，继续下钻）
            current = children[0]
            continue
        raise ModuleInstallError(
            f"无法定位插件根：{current.name} 下没有 module.json / main.py，且有 {len(children)} 个子目录",
            hint="合法插件包的根目录应直接包含 module.json 与 main.py；"
            "支持 GitHub repo-main/ 或单层包装目录，不支持把一个仓库的多个子项目打进同一个 zip",
        )

    raise ModuleInstallError(
        "压缩包的目录嵌套过深，无法定位插件根",
        hint="把插件根（含 module.json + main.py 的目录）重新打包，减少包装层级",
    )


def _looks_like_plugin(path: Path) -> bool:
    return any((path / name).is_file() for name in ("module.json", "main.py", "module.py"))


def _is_noise(name: str) -> bool:
    return name in NOISE_NAMES or name.startswith((".", "__MACOSX")) or name.lower() in {"thumbs.db", "desktop.ini"}


def _prune_noise(root: Path) -> None:
    """删除当前层的噪声目录/文件；删不掉也不阻断（它们不参与候选判定）。"""
    for child in list(root.iterdir()):
        if not _is_noise(child.name):
            continue
        try:
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        except OSError as e:
            logger.debug(f"[Module] 清理噪声条目失败（已忽略） {child}: {e}")


class ZipPluginInstaller:
    """事务化安装/删除外部插件（安装/更新用模块级 asyncio.Lock 串行）。"""

    def __init__(
        self,
        *,
        install_dir: Path | str | None,
        builtin_dir: Path | str,
        install_service: Any,
        log: Any = None,
        staging_dir: Path | str | None = None,
        backup_dir: Path | str | None = None,
    ) -> None:
        self.install_dir = Path(install_dir) if install_dir is not None else None
        self.builtin_dir = Path(builtin_dir)
        self.install_service = install_service
        self.log = log or logger
        self.staging_dir = Path(staging_dir) if staging_dir is not None else self._sibling(".staging")
        self.backup_dir = Path(backup_dir) if backup_dir is not None else self._sibling(".backup")
        self._lock = asyncio.Lock()

    # ---------- 预检（dry-run） ----------
    def preflight(self, zip_path: Path | str) -> dict:
        """只解压到 staging 做校验，不落任何目标目录；返回安装预览。"""
        self._require_install_dir()
        zip_path = Path(zip_path)
        if not zip_path.is_file():
            raise ModuleInstallError(f"插件压缩包不存在: {zip_path}", hint="重新上传 zip 文件")

        staging_root = self._make_staging()
        try:
            root = self._extract(zip_path, staging_root)
            manifest = self._read_manifest_or_fail(root)
            name = manifest.name
            self._check_name(name)
            target = self.install_dir / name
            builtin_conflict = (self.builtin_dir / name).is_dir()
            existing_version = self._existing_version(target) if target.is_dir() else ""
            conflict = bool(target.is_dir() or builtin_conflict)
            return {
                "name": name,
                "display_name": manifest.display_name,
                "version": manifest.version,
                "source": "installed",
                "target": str(target),
                "conflict": conflict,
                "conflict_reason": self._conflict_reason(name, target, builtin_conflict, existing_version),
                "builtin_conflict": builtin_conflict,
                "existing_version": existing_version,
                "files": self._count_files(root),
                "manifest": dict(manifest.raw),
                "requirements": manifest.requirements,
            }
        except ModuleInstallError:
            raise
        except Exception as e:  # noqa: BLE001 —— 预检不允许把未知异常漏成 500
            raise ModuleInstallError(
                f"预检失败: {e}",
                hint="检查 zip 是否完整、编码是否为 UTF-8、插件根下是否有 module.json",
            ) from e
        finally:
            self._cleanup(staging_root)

    # ---------- 安装 ----------
    async def install(self, zip_path: Path | str, *, force: bool = False) -> InstallResult:
        """安装/覆盖安装；失败抛 ModuleInstallError，且保证原目录可用、staging 清空。"""
        self._require_install_dir()
        zip_path = Path(zip_path)
        async with self._lock:
            if not zip_path.is_file():
                raise ModuleInstallError(f"插件压缩包不存在: {zip_path}", hint="重新上传 zip 文件")
            staging_root = self._make_staging()
            target: Path | None = None
            backup: Path | None = None
            try:
                root = self._extract(zip_path, staging_root)
                manifest = self._read_manifest_or_fail(root)
                name = manifest.name
                self._check_name(name)
                self._check_entry(root, manifest)

                if (self.builtin_dir / name).is_dir():
                    raise ModuleInstallError(
                        f"模块名 {name} 与内置模块冲突",
                        hint="内置模块不允许被外部插件覆盖；请修改 module.json 的 name 后重新打包",
                    )

                target = self.install_dir / name
                overwritten = target.exists()
                if overwritten:
                    if not force:
                        version = self._existing_version(target) or "未知"
                        raise ModuleInstallError(
                            f"模块 {name} 已安装（现有版本 {version}）",
                            hint="使用“更新/覆盖安装”来替换旧版本（旧版本会自动备份到 .backup）",
                        )
                    if not target.is_dir():
                        raise ModuleInstallError(
                            f"目标路径已被非目录占用: {target}",
                            hint="手工清理该路径后重试",
                        )
                    backup = self._backup_existing(name, target)

                files = self._count_files(root)
                try:
                    self._atomic_install(root, target)
                except Exception:
                    self._restore_backup(backup, target)
                    raise

                if self.install_service is not None:
                    self.install_service.reinstall(name)
                self._prune_backups(name)
                self.log.info(f"[Module] 已安装外部插件 {name} v{manifest.version} -> {target}")
                return InstallResult(
                    name=name,
                    display_name=manifest.display_name,
                    version=manifest.version,
                    path=target,
                    overwritten=overwritten,
                    backup=backup,
                    files=files,
                    manifest=dict(manifest.raw),
                )
            except ModuleInstallError:
                raise
            except Exception as e:  # noqa: BLE001 —— 安装失败必须转成可读错误 + hint，不允许 500
                self.log.exception(f"[Module] 安装 {zip_path} 失败: {e}")
                raise ModuleInstallError(
                    f"安装失败: {e}",
                    hint="检查 zip 是否完整、插件文件是否被占用，或重启后重试",
                ) from e
            finally:
                self._cleanup(staging_root)

    def rollback(self, result: InstallResult) -> None:
        """安装成功但加载失败时回滚：删新版本、恢复备份（由注册表调用）。"""
        target = result.path
        try:
            if target.exists():
                self._discard_dir(target, "rollback")
        except OSError as e:
            raise ModuleInstallError(
                f"回滚失败：无法删除新版本目录 {target}: {e}",
                hint=f"手工删除 {target} 后重试",
            ) from e
        if result.backup is not None and result.backup.exists():
            try:
                os.replace(result.backup, target)
            except OSError as e:
                raise ModuleInstallError(
                    f"回滚失败：无法恢复备份 {result.backup}: {e}",
                    hint=f"手工把 {result.backup} 移回 {target}",
                ) from e
            self.log.warning(f"[Module] {result.name} 加载失败，已回滚到安装前版本")
        else:
            self.log.warning(f"[Module] {result.name} 加载失败，已删除新安装目录 {target}")

    # ---------- 删除 ----------
    async def delete(self, name: str, *, purge_data_dir: Path | str | None = None) -> dict:
        """硬删除外部插件目录（仅第三方；内置由注册表拒绝），可选清理运行时数据。"""
        self._require_install_dir()
        self._check_name(name)
        async with self._lock:
            target = self.install_dir / name
            if not target.is_dir():
                raise ModuleInstallError(f"模块 {name} 未安装", hint="外部插件目录下没有该模块")
            # 先改名再删：安装目录原子地"消失"，不会出现删一半的中间态
            try:
                self._discard_dir(target, f"delete-{name}")
            except OSError as e:
                raise ModuleInstallError(
                    f"删除失败: {e}",
                    hint="插件文件可能被占用；关闭占用程序或重启后重试",
                ) from e
            data_path: Path | None = None
            if purge_data_dir is not None:
                data_path = Path(purge_data_dir) / name
                if data_path.is_dir():
                    try:
                        shutil.rmtree(data_path)
                    except OSError as e:
                        raise ModuleInstallError(
                            f"插件已删除，但清理数据目录失败: {e}",
                            hint=f"手工删除 {data_path}",
                        ) from e
            if self.install_service is not None:
                self.install_service.reinstall(name)
            self._remove_backups(name)
            self.log.info(f"[Module] 已删除外部插件 {name}")
            return {
                "name": name,
                "source": "installed",
                "path": str(target),
                "deleted": True,
                "purge_data": purge_data_dir is not None,
                "data_removed": purge_data_dir is not None and not (data_path and data_path.exists()),
            }

    # ---------- 解压 / 校验 ----------
    def _extract(self, zip_path: Path, dest: Path) -> Path:
        """安全解压并按布局容错归一插件根，返回插件根路径。"""
        try:
            archive = zipfile.ZipFile(zip_path, "r")
        except zipfile.BadZipFile as e:
            raise ModuleInstallError(
                "插件压缩包格式错误（不是合法 zip）",
                hint="重新打包为 .zip（不要上传 .7z/.rar/改名后的文件）",
            ) from e

        with archive:
            infos = archive.infolist()
            if not infos:
                raise ModuleInstallError("插件压缩包为空", hint="重新打包插件目录")
            file_infos = [info for info in infos if not info.is_dir()]
            if not file_infos:
                raise ModuleInstallError("插件压缩包里没有文件", hint="重新打包插件目录")
            if len(file_infos) > MAX_ARCHIVE_FILES:
                raise ModuleInstallError(
                    f"插件压缩包文件数过多（{len(file_infos)} > {MAX_ARCHIVE_FILES}）",
                    hint="不要打包虚拟环境/依赖缓存（venv、node_modules、__pycache__）",
                )
            total_bytes = sum(max(info.file_size, 0) for info in file_infos)
            if total_bytes > MAX_ARCHIVE_BYTES:
                raise ModuleInstallError(
                    f"插件解压后体积过大（{total_bytes // (1024 * 1024)}MB > {MAX_ARCHIVE_BYTES // (1024 * 1024)}MB）",
                    hint="移除大文件/模型/依赖缓存后重新打包",
                )

            mappings = [(info, self._safe_parts(info)) for info in infos]
            dest.mkdir(parents=True, exist_ok=True)
            for info, parts in mappings:
                if not parts:
                    continue
                target = dest.joinpath(*parts)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info, "r") as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out, 1024 * 1024)

        return normalize_plugin_root(dest)

    def _safe_parts(self, info: zipfile.ZipInfo) -> list[str]:
        """校验单个 zip 条目并返回安全路径分段（拒绝穿越/绝对路径/符号链接）。"""
        name = self._decode_name(info)
        normalized = name.replace("\\", "/")
        if _DRIVE_PATTERN.match(normalized):
            raise ModuleInstallError(
                f"压缩包含盘符绝对路径: {name}",
                hint="用标准的相对路径重新打包（不要在压缩包里塞 C:/ 这类路径）",
            )
        if normalized.startswith("/"):
            raise ModuleInstallError(
                f"压缩包含绝对路径: {name}",
                hint="用标准的相对路径重新打包",
            )
        parts = [part for part in normalized.split("/") if part not in ("", ".")]
        if any(part == ".." for part in parts):
            raise ModuleInstallError(
                f"压缩包含路径穿越: {name}",
                hint="移除 .. 路径后重新打包（该 zip 可能经过恶意构造）",
            )
        if not parts:
            return []
        if len(parts) > MAX_ENTRY_DEPTH:
            raise ModuleInstallError(
                f"压缩包目录层级过深（>{MAX_ENTRY_DEPTH} 层）: {name}",
                hint="减少包装层级后重新打包",
            )
        if any(":" in part for part in parts):
            raise ModuleInstallError(
                f"压缩包含 Windows 非法文件名（含 ':'）: {name}",
                hint="重命名后重新打包",
            )
        if self._is_symlink(info):
            raise ModuleInstallError(
                f"压缩包含符号链接: {name}",
                hint="插件包不允许符号链接（可能指向任意路径）",
            )
        return parts

    @staticmethod
    def _decode_name(info: zipfile.ZipInfo) -> str:
        """条目无 UTF-8 标志时按 cp437→gb18030 回退重解（Windows 中文名 zip）。

        zipfile 对未置 UTF-8 标志的条目一律用 cp437 解码，中文压缩包会变乱码；
        这里把 cp437 的"字节视图"还原后按 gb18030 再解一次，失败则保留原样。
        """
        name = info.filename
        if info.flag_bits & 0x800:
            return name
        try:
            raw = name.encode("cp437")
        except UnicodeEncodeError:
            return name
        try:
            return raw.decode("gb18030")
        except UnicodeDecodeError:
            return name

    @staticmethod
    def _is_symlink(info: zipfile.ZipInfo) -> bool:
        mode = (info.external_attr >> 16) & 0xFFFF
        return stat.S_ISLNK(mode)

    def _read_manifest_or_fail(self, root: Path) -> Manifest:
        try:
            manifest = load_manifest(root)
        except ManifestError as e:
            raise ModuleInstallError(
                f"插件清单不可用: {e}",
                hint=e.hint or "补齐 module.json（name/display_name/version 必填）后重新打包",
            ) from e
        self._check_name(manifest.name)
        return manifest

    def _check_name(self, name: str) -> None:
        if not NAME_PATTERN.match(name):
            raise ModuleInstallError(
                f"非法模块名: {name!r}",
                hint="name 只允许字母/数字/下划线，不能以数字开头，不允许 '.'、':'、中文、空格",
            )
        if name.upper() in _WINDOWS_RESERVED:
            raise ModuleInstallError(
                f"模块名 {name} 是 Windows 保留设备名，无法创建目录",
                hint="换一个 name（如 my_con）后重新打包",
            )

    @staticmethod
    def _check_entry(root: Path, manifest: Manifest) -> None:
        """安装期就校验入口文件（AstrBot 的已知坑 ①：装上了但没入口）。"""
        explicit_entry = str(manifest.raw.get("entry") or "").strip()
        entry_name = explicit_entry or manifest.entry or DEFAULT_ENTRY
        if (root / entry_name).is_file():
            return
        if not explicit_entry and (root / "module.py").is_file():
            # 旧插件兼容：loader 仍支持 module.py 作为入口
            return
        raise ModuleInstallError(
            f"插件入口文件不存在: {entry_name}",
            hint=f"插件根下必须有 {entry_name}（导出 Module 类）；若入口另有其名，请在 module.json 里写 entry",
        )

    # ---------- 目录操作 ----------
    def _atomic_install(self, root: Path, target: Path) -> None:
        """把 staging 里的插件根原子换入目标目录（同分区用 os.replace）。"""
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(root, target)
        except OSError as e:
            # staging 配到别的分区时 os.replace 会失败：退化为 move（已备份，失败由调用方回滚）
            self.log.debug(f"[Module] os.replace 失败，退化为 move: {e}")
            shutil.move(str(root), str(target))

    def _backup_existing(self, name: str, target: Path) -> Path:
        assert self.backup_dir is not None
        try:
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            destination = self.backup_dir / f"{name}-{stamp}"
            if destination.exists():
                destination = self.backup_dir / f"{name}-{stamp}-{uuid.uuid4().hex[:6]}"
            try:
                os.replace(target, destination)
            except OSError:
                # .backup 配到别的分区时 rename 不可用，退化为 move（旧版本已完整复制后才删源）
                shutil.move(str(target), str(destination))
            return destination
        except OSError as e:
            raise ModuleInstallError(
                f"备份旧版本失败: {e}",
                hint="检查 data/module/.backup 是否可写、旧版本文件是否被占用",
            ) from e

    def _restore_backup(self, backup: Path | None, target: Path) -> None:
        if backup is None or not backup.exists():
            return
        try:
            if target.exists():
                shutil.rmtree(target)
            os.replace(backup, target)
        except OSError as e:
            self.log.warning(f"[Module] 安装失败后恢复备份失败，请手工处理 {backup} -> {target}: {e}")

    def _prune_backups(self, name: str) -> None:
        """只保留最近 BACKUP_KEEP 份（按目录名里的时间戳排序）。"""
        if self.backup_dir is None or not self.backup_dir.is_dir():
            return
        try:
            candidates = sorted(
                (path for path in self.backup_dir.iterdir() if path.is_dir() and path.name.startswith(f"{name}-")),
                key=lambda path: path.name,
            )
        except OSError as e:
            self.log.debug(f"[Module] 读取备份目录失败（已忽略）: {e}")
            return
        for old in candidates[: max(0, len(candidates) - BACKUP_KEEP)]:
            try:
                shutil.rmtree(old)
            except OSError as e:
                self.log.debug(f"[Module] 清理旧备份失败（已忽略） {old}: {e}")

    def _remove_backups(self, name: str) -> None:
        if self.backup_dir is None or not self.backup_dir.is_dir():
            return
        try:
            candidates = [path for path in self.backup_dir.iterdir() if path.name.startswith(f"{name}-")]
        except OSError as e:
            self.log.debug(f"[Module] 读取备份目录失败（已忽略）: {e}")
            return
        for old in candidates:
            try:
                shutil.rmtree(old) if old.is_dir() else old.unlink()
            except OSError as e:
                self.log.debug(f"[Module] 清理备份失败（已忽略） {old}: {e}")

    def _discard_dir(self, target: Path, label: str) -> None:
        """把目录改名到 staging 再删除：安装目录先原子消失，再慢慢清理。"""
        trash_root = self.staging_dir or (self.install_dir.parent / ".staging" if self.install_dir else None)
        if trash_root is None:
            shutil.rmtree(target)
            return
        trash_root.mkdir(parents=True, exist_ok=True)
        trash = trash_root / f"{label}-{uuid.uuid4().hex[:8]}"
        os.replace(target, trash)
        try:
            shutil.rmtree(trash)
        except OSError as e:
            self.log.warning(f"[Module] 待删除目录清理失败（已从安装目录移除）{trash}: {e}")

    def _make_staging(self) -> Path:
        assert self.staging_dir is not None
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        path = self.staging_dir / uuid.uuid4().hex
        path.mkdir(parents=True)
        return path

    def _cleanup(self, path: Path) -> None:
        try:
            shutil.rmtree(path)
        except FileNotFoundError:
            pass
        except OSError as e:
            self.log.warning(f"[Module] staging 清理失败（残留，可手工删）{path}: {e}")

    def _sibling(self, name: str) -> Path | None:
        return self.install_dir.parent / name if self.install_dir is not None else None

    def _require_install_dir(self) -> None:
        if self.install_dir is None:
            raise ModuleInstallError(
                "未配置外部插件安装目录",
                hint="在 settings 中配置 module_dir（默认 data/module），外部插件装到 data/module/plugins",
            )

    @staticmethod
    def _count_files(root: Path) -> int:
        try:
            return sum(1 for path in root.rglob("*") if path.is_file())
        except OSError:
            return 0

    @staticmethod
    def _existing_version(target: Path) -> str:
        if not (target / "module.json").is_file():
            return ""
        try:
            return load_manifest(target).version
        except (ManifestError, OSError):
            return ""

    @staticmethod
    def _conflict_reason(name: str, target: Path, builtin_conflict: bool, existing_version: str) -> str:
        if builtin_conflict:
            return f"与内置模块 {name} 同名，安装会被拒绝（内置优先）"
        if target.is_dir():
            return f"已安装 {name}" + (f" v{existing_version}" if existing_version else "") + "，需要覆盖安装"
        return ""
