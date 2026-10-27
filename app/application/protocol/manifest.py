"""协议端版本清单：内置 pin + 用户覆盖，只认清单内的版本。

清单形状（``data/protocol/manifest.json``，可选覆盖内置）::

    {
      "schema": "qqbotnext.protocol/1",
      "protocols": {
        "napcat": {
          "display_name": "NapCat",
          "homepage": "https://napneko.github.io/",
          "versions": [
            {"version": "1.2.0", "url": "https://.../napcat-1.2.0.zip",
             "sha256": "<64 hex>", "size": 12345678, "note": "推荐"}
          ]
        }
      }
    }
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SCHEMA = "qqbotnext.protocol/1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.-]+)?$")

#: 内置清单：默认不带任何下载地址（避免指向未验证的第三方构建）。
#: 用户可在 ``data/protocol/manifest.json`` 里补充自己校验过的版本。
BUILTIN_MANIFEST: dict = {
    "schema": SCHEMA,
    "protocols": {
        "napcat": {
            "display_name": "NapCat",
            "homepage": "https://napneko.github.io/",
            "versions": [],
        },
        "lagrange": {
            "display_name": "Lagrange.OneBot",
            "homepage": "https://github.com/LagrangeDev/Lagrange.Core",
            "versions": [],
        },
    },
}


class ProtocolError(ValueError):
    """协议端操作失败（清单/版本/校验/状态）。"""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.hint = hint


def _check_version_entry(entry: Any, protocol: str) -> dict:
    if not isinstance(entry, dict):
        raise ProtocolError("PROTOCOL_MANIFEST_INVALID", f"{protocol} 的版本条目必须是对象")
    version = str(entry.get("version") or "").strip()
    if not _SEMVER_RE.match(version):
        raise ProtocolError("PROTOCOL_VERSION_INVALID", f"{protocol} 版本号不是 semver：{version or '(空)'}")
    url = str(entry.get("url") or "").strip()
    if not url.startswith(("https://", "http://")):
        raise ProtocolError(
            "PROTOCOL_MANIFEST_INVALID", f"{protocol} {version} 缺少 http(s) 下载地址",
            "清单里必须写明 url，不接受运行时拼地址",
        )
    sha256 = str(entry.get("sha256") or "").strip().lower()
    if not _SHA256_RE.match(sha256):
        raise ProtocolError(
            "PROTOCOL_MANIFEST_INVALID", f"{protocol} {version} 的 sha256 缺失或格式不对",
            "下载校验是强制的：没有 sha256 的版本不会被接受",
        )
    size = entry.get("size")
    return {
        "version": version,
        "url": url,
        "sha256": sha256,
        "size": int(size) if isinstance(size, int) and size > 0 else 0,
        "note": str(entry.get("note") or "").strip(),
    }


def normalize_manifest(raw: Any) -> dict:
    """校验并归一化清单；非法直接抛错（不静默忽略，避免"以为是 pin 版"）。"""
    if not isinstance(raw, dict):
        raise ProtocolError("PROTOCOL_MANIFEST_INVALID", "清单必须是 JSON 对象")
    if str(raw.get("schema") or "") != SCHEMA:
        raise ProtocolError(
            "PROTOCOL_SCHEMA_UNSUPPORTED", f"不支持的计划格式：{raw.get('schema') or '(空)'}"
        )
    protocols_raw = raw.get("protocols")
    if not isinstance(protocols_raw, dict):
        raise ProtocolError("PROTOCOL_MANIFEST_INVALID", "清单缺少 protocols")

    protocols: dict[str, dict] = {}
    for name, entry in protocols_raw.items():
        if not isinstance(entry, dict):
            continue
        versions = [_check_version_entry(item, str(name)) for item in entry.get("versions") or []]
        # 高版本在前，便于 UI 默认选中最新 pin 版
        versions.sort(key=lambda item: tuple(int(part) for part in item["version"].split(".")[:3]),
                      reverse=True)
        protocols[str(name)] = {
            "display_name": str(entry.get("display_name") or name),
            "homepage": str(entry.get("homepage") or "").strip(),
            "versions": versions,
        }
    return {"schema": SCHEMA, "protocols": protocols}


def load_manifest(data_root: Path | str) -> dict:
    """内置清单 + ``<data_root>/protocol/manifest.json`` 覆盖（用户加的版本追加进来）。"""
    manifest = normalize_manifest(json.loads(json.dumps(BUILTIN_MANIFEST)))
    override_path = Path(data_root) / "protocol" / "manifest.json"
    if not override_path.is_file():
        return manifest
    try:
        override = normalize_manifest(json.loads(override_path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as e:
        raise ProtocolError("PROTOCOL_MANIFEST_INVALID", f"自定义清单读取失败：{e}") from e
    for name, entry in override["protocols"].items():
        base = manifest["protocols"].get(name) or {"display_name": name, "homepage": "", "versions": []}
        known = {item["version"] for item in base["versions"]}
        merged = list(base["versions"]) + [
            item for item in entry["versions"] if item["version"] not in known
        ]
        merged.sort(key=lambda item: tuple(int(part) for part in item["version"].split(".")[:3]),
                    reverse=True)
        manifest["protocols"][name] = {
            "display_name": entry["display_name"] or base["display_name"],
            "homepage": entry["homepage"] or base["homepage"],
            "versions": merged,
        }
    return manifest


def select_version(
    manifest: dict,
    protocol: str,
    version: str | None = None,
) -> dict:
    """选版本：``None`` = 清单里最高的 pin 版；指定版本必须在清单内。"""
    entry = (manifest.get("protocols") or {}).get(protocol)
    if entry is None:
        raise ProtocolError("PROTOCOL_UNKNOWN", f"不认识的协议端：{protocol}")
    versions = entry.get("versions") or []
    if not versions:
        raise ProtocolError(
            "PROTOCOL_NO_VERSION",
            f"{entry['display_name']} 还没有配置可安装的版本",
            "在 data/protocol/manifest.json 里加入你校验过的版本（url + sha256）",
        )
    if version is None:
        return versions[0]
    for item in versions:
        if item["version"] == str(version):
            return item
    raise ProtocolError(
        "PROTOCOL_VERSION_UNKNOWN", f"{entry['display_name']} 没有清单内的版本 {version}",
        "只允许安装清单里固定过的版本",
    )
