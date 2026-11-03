"""OneBot API 工具清单（数据在 ``manifest.json``，本文件只负责加载与派生）。

## 为什么清单是 JSON 而不是 Python 字面量

这里原本是 4,792 行的 `ONEBOT_TOOLS: list[dict] = [...]` 字面量——它是**数据**，
不是代码，却占了整个目录的绝大部分篇幅，让「安全策略在哪」「执行逻辑在哪」都看不见。
改成 `manifest.json` 之后：

- 加一条工具 = 改一小段 JSON（不用在 4,792 行的 Python diff 里定位）；
- 结构可被测试校验（`tests/infrastructure/test_onebot_manifest_data.py` 检查必填
  字段、枚举值、重名、`doc_url` 形态）；
- 目录里只剩少量职责单一的 .py：本文件（加载/派生）与 ``executor.py``（执行）。

## 每条记录的字段

- ``name``: 对应 OneBot action
- ``description``: 给 LLM 看的说明
- ``parameters``: OpenAI function calling 参数 schema
- ``risk``: read / send / admin
- ``permission``: 清单默认权限（member / group_admin / group_owner / owner）
- ``scopes``: group / private / ``["*"]``
- ``category`` / ``doc_url`` / ``action``（可选）

## ``sensitivity`` 派生规则（2026-11-03 重写）

``sensitivity`` 不写进 JSON，由本文件按**两条规则**派生（critical 优先）：

| 等级 | 判定 | 授权要求（消息凭证鉴权） |
|---|---|---|
| ``critical`` | 修改/退出**自身账号**或账号级数据的工具（资料、头像、签名、在线状态、好友、自定义表情、收藏/空间、原始数据包、缓存/重启…） | bot **拥有者**（owner） |
| ``high`` | 清单 ``permission`` 为 **群管理员及以上**（group_admin / group_owner / owner）的工具 | bot **管理者或拥有者**（manager / owner） |
| ``normal`` | 其余（member/everyone） | 按原工具权限 |

> 旧口径是「高危不可撤销操作（踢人/禁言/退群…）」= high、critical；新口径把
> 「群管理类」统一为 high、「自身账号类」提升为 critical，并改为按 **bot 账号侧**
> 身份授权（见 ``security.SENSITIVITY_AUTHORIZER_ROLES``）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MANIFEST_PATH = Path(__file__).with_name("manifest.json")

#: critical：修改/退出**自身账号**或账号级数据的工具（授权需要 bot 拥有者）。
#: 这些操作动的是"这个号本身"，比群管理更敏感，因此单列而不是从 permission 推导。
_CRITICAL_TOOLS: frozenset[str] = frozenset({
    "_set_model_show",            # 设置当前账号的设备机型
    "bot_exit",                   # 退出登录
    "clean_cache",                # 清理协议端缓存
    "create_collection",          # 创建收藏
    "delete_custom_face",         # 删除自定义表情
    "delete_friend",              # 删除好友
    "delete_qzone_msg",           # 删除空间说说
    "send_packet",                # 发送原始数据包（等同账号级操作）
    "send_qzone_msg",             # 发空间说说
    "set_custom_face_desc",       # 修改自定义表情描述
    "set_diy_online_status",      # 自定义在线状态
    "set_doubt_friends_add_request",  # 处理可疑好友申请
    "set_friend_add_request",     # 处理好友申请
    "set_friend_remark",          # 设置好友备注
    "set_online_status",          # 在线状态
    "set_qq_avatar",              # 修改头像
    "set_qq_profile",             # 修改昵称/签名等资料
    "set_restart",                # 重启服务
    "set_self_longnick",          # 修改个性签名
})

#: high：清单默认「群管理员及以上」才能用的工具（授权需要 bot 管理者/拥有者）。
_HIGH_PERMISSIONS: frozenset[str] = frozenset({"group_admin", "group_owner", "owner"})


def _load_catalog() -> list[dict[str, Any]]:
    """读 JSON 清单，并为每条记录补上派生的 ``sensitivity``。"""
    raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise TypeError(f"{MANIFEST_PATH.name} 顶层必须是数组，实际是 {type(raw).__name__}")
    catalog: list[dict[str, Any]] = []
    for entry in raw:
        name = str(entry.get("name", ""))
        permission = str(entry.get("permission", "member") or "member")
        if name in _CRITICAL_TOOLS:
            entry["sensitivity"] = "critical"
        elif permission in _HIGH_PERMISSIONS:
            entry["sensitivity"] = "high"
        else:
            entry["sensitivity"] = "normal"
        catalog.append(entry)
    return catalog


ONEBOT_TOOLS: list[dict[str, Any]] = _load_catalog()

#: 当前清单里被判定为 high 的工具名（供测试与文档核对；由 permission 派生）。
_HIGH_TOOLS: frozenset[str] = frozenset(
    str(tool["name"]) for tool in ONEBOT_TOOLS if tool.get("sensitivity") == "high"
)

__all__ = [
    "MANIFEST_PATH",
    "ONEBOT_TOOLS",
    "_CRITICAL_TOOLS",
    "_HIGH_PERMISSIONS",
    "_HIGH_TOOLS",
]
