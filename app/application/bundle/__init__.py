"""配置套装（Bundle）工件：清单解析 / 校验 / 导出。

设计依据：`docs/oneclick-bundle-and-onboarding-design.md` §5。
本子包只处理"工件本身"（zip 字节 + 明细），**不写账号配置**——
应用/回滚在 P3（`bundle_activations` + 事务）里做。
"""

from __future__ import annotations

from app.application.bundle.errors import BundleError
from app.application.bundle.manifest import BUNDLE_SCHEMA, normalize_manifest

__all__ = ["BUNDLE_SCHEMA", "BundleError", "normalize_manifest"]
