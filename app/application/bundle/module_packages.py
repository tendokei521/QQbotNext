"""随包模块（zip 代码包）：打包已安装模块 / 从 zip 识别模块身份。

预设里的模块代码走**既有 zip 安装通道**（`ModuleRegistry.install_from_zip`），
本模块只负责"打包"和"识别"，不安装、不加载、不碰运行状态——执行面仍只有一条。

两条硬规矩：
1. **确定性打包**：文件排序 + 固定时间戳，同目录两次打包字节一致（便于校验和/去重）；
2. **只打包模块根**：`module.json` 必须在 zip 根（与安装器 `normalize_plugin_root` 的
   判定一致），噪声目录（`__pycache__` / `.git` / `venv` 等）不进包。
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from app.application.bundle.errors import BundleError

#: 模块清单文件名（安装器认它来定位插件根）
MODULE_MANIFEST = "module.json"

#: 打包时丢弃的噪声目录/文件（与插件安装器 `NOISE_NAMES` 同口径）
NOISE_NAMES = frozenset({
    "__pycache__", "__MACOSX", ".git", ".github", ".idea", ".vscode",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".venv", "venv",
    "node_modules", ".DS_Store", "Thumbs.db", "desktop.ini",
})

#: 单个模块包的上限（超过就拒绝随包，避免预设被撑爆；安装器上限同为 200 MB）
MAX_PACKAGE_BYTES = 50 * 1024 * 1024
MAX_PACKAGE_FILES = 5000

#: 固定时间戳：保证同内容 → 同字节
_FIXED_TIME = (2020, 1, 1, 0, 0, 0)


class ModulePackageError(BundleError):
    """随包模块不可用（目录不存在/超限/不是合法模块包）。"""


def _is_noise(path: Path) -> bool:
    return any(part in NOISE_NAMES or part.startswith(".") for part in path.parts)


def _iter_payloads(root: Path) -> list[tuple[str, bytes]]:
    """收集模块根下要打包的文件（相对路径 + 字节），跳过噪声与符号链接。"""
    payloads: list[tuple[str, bytes]] = []
    total = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root)
        if _is_noise(relative):
            continue
        try:
            data = path.read_bytes()
        except OSError as e:
            raise ModulePackageError(
                "BUNDLE_MODULE_PACKAGE_INVALID",
                f"读取模块文件失败：{relative}（{e}）",
                "检查文件是否被占用后重试",
            ) from e
        total += len(data)
        payloads.append((relative.as_posix(), data))
        if len(payloads) > MAX_PACKAGE_FILES:
            raise ModulePackageError(
                "BUNDLE_MODULE_PACKAGE_TOO_LARGE",
                f"模块文件数超过 {MAX_PACKAGE_FILES}",
                "不要打包虚拟环境/依赖缓存（venv、node_modules、__pycache__）",
            )
        if total > MAX_PACKAGE_BYTES:
            raise ModulePackageError(
                "BUNDLE_MODULE_PACKAGE_TOO_LARGE",
                f"模块体积超过 {MAX_PACKAGE_BYTES // (1024 * 1024)} MB",
                "移除大文件后重新导出，或让对方自行安装该模块",
            )
    return payloads


def pack_module_dir(root: Path | str) -> bytes:
    """把一个已安装模块目录打包成可被 `install_from_zip` 接受的 zip 字节。"""
    directory = Path(root)
    if not directory.is_dir():
        raise ModulePackageError(
            "BUNDLE_MODULE_PACKAGE_INVALID",
            f"模块目录不存在：{directory}",
            "确认模块是否已被删除/移动",
        )
    if not (directory / MODULE_MANIFEST).is_file():
        raise ModulePackageError(
            "BUNDLE_MODULE_PACKAGE_INVALID",
            f"模块目录缺少 {MODULE_MANIFEST}：{directory}",
            "内置/自定义页面目录不是模块根，无法随包导出",
        )
    payloads = _iter_payloads(directory)
    if not payloads:
        raise ModulePackageError("BUNDLE_MODULE_PACKAGE_INVALID", f"模块目录为空：{directory}")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in payloads:
            info = zipfile.ZipInfo(name, date_time=_FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    return buffer.getvalue()


def read_module_identity(data: bytes) -> dict:
    """从模块 zip 字节里读出 ``{name, version, display_name}``。

    只认 zip 根或**单层包装目录**下的 `module.json`（与安装器布局容错口径一致：
    GitHub 的 `repo-main/` 这类包装是允许的）；找不到/不合法 → `BundleError`。
    """
    if not data:
        raise ModulePackageError("BUNDLE_MODULE_PACKAGE_INVALID", "模块包为空")
    try:
        with zipfile.ZipFile(io.BytesIO(bytes(data))) as archive:
            candidates = []
            for name in archive.namelist():
                normalized = name.replace("\\", "/")
                if normalized.endswith("/" + MODULE_MANIFEST) or normalized == MODULE_MANIFEST:
                    depth = normalized.count("/")
                    candidates.append((depth, normalized))
            if not candidates:
                raise ModulePackageError(
                    "BUNDLE_MODULE_PACKAGE_INVALID",
                    "模块包里没有 module.json",
                    "模块 zip 的根目录（或唯一一层包装目录）里必须有 module.json",
                )
            candidates.sort()
            _, manifest_name = candidates[0]
            raw = archive.read(manifest_name)
    except zipfile.BadZipFile as e:
        raise ModulePackageError("BUNDLE_MODULE_PACKAGE_INVALID", f"模块包不是合法 zip：{e}") from e
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ModulePackageError(
            "BUNDLE_MODULE_PACKAGE_INVALID", f"模块包的 module.json 不是合法 JSON：{e}"
        ) from e
    if not isinstance(manifest, dict):
        raise ModulePackageError("BUNDLE_MODULE_PACKAGE_INVALID", "模块包的 module.json 必须是对象")
    name = str(manifest.get("name") or "").strip()
    version = str(manifest.get("version") or "").strip()
    if not name:
        raise ModulePackageError("BUNDLE_MODULE_PACKAGE_INVALID", "模块包的 module.json 缺少 name")
    return {
        "name": name,
        "version": version,
        "display_name": str(manifest.get("display_name") or name).strip(),
    }
