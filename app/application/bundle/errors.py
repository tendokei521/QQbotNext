"""套装操作的统一异常：带稳定错误码，API 可直接回给前端。"""

from __future__ import annotations


class BundleError(ValueError):
    """套装不可用（结构/兼容/依赖/引用/安全）。"""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.hint = hint
