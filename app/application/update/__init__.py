"""可回滚更新（计划 P5-5）。

三条底线：
1. **先备份再动手**：``data/`` 里的数据库与 ``.env`` 在更新前复制到
   ``data/updates/<ver>/backup/``；
2. **被替换的文件留底**：旧 ``app/`` 与根目录入口文件复制到 ``previous/``，
   出问题能整体退回去；
3. **先暂存后切换**：下载的发行包先解压到 ``data/updates/<ver>/stage/``，
   校验通过了才动线上文件，任何一步失败都保持原样。

版本比对与下载器都从外部注入，单测不联网。
"""

from __future__ import annotations

from app.application.update.updater import UpdateError, UpdateService
from app.application.update.version import compare_versions, read_local_version

__all__ = ["UpdateError", "UpdateService", "compare_versions", "read_local_version"]
