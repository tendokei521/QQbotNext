"""协议端（NapCat / Lagrange 等）托管：固定版本清单与可校验安装。

设计依据：`docs/oneclick-bundle-and-onboarding-design.md` §8 / 计划 P5-1/P5-2。

两条硬规矩：
1. **只用清单里的固定版本**（pin），不允许"装最新"——版本漂移会让连接行为不可复现；
2. **下载必须校验 sha256**，校验失败直接拒绝，绝不落盘成"半装"状态。
"""

from __future__ import annotations

from app.application.protocol.installer import ProtocolInstaller
from app.application.protocol.manifest import (
    ProtocolError,
    load_manifest,
    select_version,
)

__all__ = ["ProtocolError", "ProtocolInstaller", "load_manifest", "select_version"]
