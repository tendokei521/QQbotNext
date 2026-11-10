"""官方 QQ 机器人协议适配（QQ 开放平台 API v2）。

与 OneBot 适配器并列：产出同一套 ``app.domain.events``、实现同一个 ``IBot``。
模块只依赖领域事件与 ``IBot``，因此插件/Agent 不需要知道账号是哪种协议。

目录：

- ``auth``       AppID/AppSecret → access_token（缓存 + 提前刷新）
- ``api``        HTTPS OpenAPI（鉴权头 / err_code / 限频）
- ``ws``         事件接入：/gateway → Hello → Identify → 心跳 → Resume
- ``codec``      官方事件 → 领域事件（字符串 openid、附件、引用、角色）
- ``inbox``      msg_id 去重 + 被动回复窗口（msg_id/msg_seq）
- ``connection`` IBot 实现（文本收发 / 撤回 / 群列表模拟 / 能力声明）
- ``gateway``    账号生命周期（连接、重连、状态广播、诊断历史）
"""

from app.infrastructure.qqbot.api import API_BASE, QqBotAPI, QqBotAPIError
from app.infrastructure.qqbot.auth import TokenManager
from app.infrastructure.qqbot.connection import QqBotConnection
from app.infrastructure.qqbot.gateway import QqBotGateway

__all__ = [
    "API_BASE",
    "QqBotAPI",
    "QqBotAPIError",
    "QqBotConnection",
    "QqBotGateway",
    "TokenManager",
]
