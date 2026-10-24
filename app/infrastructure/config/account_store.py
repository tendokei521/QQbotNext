"""账号存储（`ConfigService` 的账号半边）。

## 为什么单独成文件

`config_service.py` 同时装着「账号身份」「模块配置/权限」「Provider 预设」「角色预设」
四套互不相干的东西，早就顶到 800 行闸门。账号这块自成闭环（id/name 规则、token
回填、上次登录快照），抽出来既让主文件回到闸门内，也让"账号身份的真相"只有一个文件。

以 mixin 形式提供（不是独立类）：它需要 `ConfigService` 的内存缓存（`_bots` /
`_bot_accounts`）与变更通知（`_notify`），拆成两个对象反而要在中间维护同步。

## 身份约定

- `id`：用户添加账号时设定，**唯一且不可变**，是所有 key 的来源（前后端判断用）；
- `name`：展示名，可随时改；日志与界面一律用它；
- 登录的 QQ 号只作为「上次登录快照」保存，不再是身份（见 `bot_accounts` 表）。
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

from app.core.logger import set_account_labels
from app.infrastructure.config.ws_url import (
    ACCESS_TOKEN_MASK,
    join_ws_url,
    split_ws_url,
)
from app.infrastructure.onebot.endpoint import FORWARD, normalize_mode
from app.infrastructure.persistence.account_migration import (
    normalize_account_id,
    normalize_account_name,
    suggest_account_id,
)


class AccountStoreMixin:
    """账号配置与「上次登录快照」的读写（由 ConfigService 组合使用）。"""

    # ---- 由 ConfigService 提供的状态（仅作类型提示，便于静态检查）----
    db: Any
    log: Any
    _bots: dict[str, dict]
    _bot_accounts: dict[str, dict]

    async def _notify(self, scope: str, payload: Any = None) -> None:  # pragma: no cover
        raise NotImplementedError

    # ==================== bots ====================
    def get_bots(self) -> list[dict]:
        """内部使用：返回真实 ws_url（网关/连接），按界面顺序排列。"""
        return [dict(b) for b in self._bots.values()]

    def get_bot(self, account_id: str) -> dict | None:
        """取单个账号配置（含 id/name/真实 ws_url）。"""
        cfg = self._bots.get(str(account_id))
        return dict(cfg) if cfg else None

    def get_account_name(self, account_id: Any) -> str:
        """账号展示名（日志与界面统一用它；查不到就退回 id 本身，绝不返回空串）。"""
        cfg = self._bots.get(str(account_id))
        if cfg and cfg.get("name"):
            return str(cfg["name"])
        return str(account_id) if account_id is not None else "全局"

    def account_ids(self) -> list[str]:
        return list(self._bots.keys())

    def _publish_account_labels(self) -> None:
        """把 id → 展示名映射交给日志层：日志前缀一律打名字，不打 id。"""
        set_account_labels({aid: cfg.get("name") or aid for aid, cfg in self._bots.items()})

    def get_bots_public(self) -> list[dict]:
        """对外使用（WebUI 配置接口）：ws_url 拆出基础地址，access_token 独立字段回显真实值。

        配置页需要直观查看/编辑 token，因此不在这里打码——安全由接口鉴权
        （WEBUI_TOKEN）与传输层（HTTPS 反代）保证；页面 HTML 渲染走
        gateway.get_bots_info()（纯地址，不含 token）。
        """
        result = []
        for account_id, b in self._bots.items():
            base, token = split_ws_url(b.get("ws_url", ""))
            result.append({
                "id": account_id,
                "name": b.get("name") or account_id,
                "owner_id": b.get("owner_id"),
                "auto_connect": bool(b.get("auto_connect")),
                "conn_mode": normalize_mode(b.get("conn_mode")),
                "ws_url": base,
                "access_token": token,
            })
        return result

    async def save_bots(self, bots: list[dict]) -> None:
        """保存账号配置（按 id 全量 upsert；payload 里没有的 id 视为删除）。

        支持独立 access_token 字段（WebUI 独立输入框）：

        - access_token 字段缺失 或 值为打码哨兵 → 保留旧 token（同 id 优先、基础地址匹配兜底）；
        - access_token 为显式值（含空串）→ 直接采用（空串 = 清除 token）；
        - 兼容旧前端：ws_url 本身携带打码 access_token 时同样回填；
        - conn_mode 缺字段/非法值 → 沿用该 id 的旧值（新账号归一为 forward）；
        - name 缺字段 → 沿用旧名；id 非法或重复 → 抛 ValueError（由 API 转 400）。

        顺序即 payload 顺序（写进 sort_order），因为 id 不可变、不能靠下标表达顺序。
        """
        old = self._bots
        normalized: dict[str, dict] = {}
        for cfg in bots:
            account_id = normalize_account_id(cfg.get("id"))
            if not account_id:
                raise ValueError(f"账号 id 非法：{cfg.get('id')!r}（只允许字母/数字/下划线/连字符）")
            if account_id in normalized:
                raise ValueError(f"账号 id 重复：{account_id}")

            prev = old.get(account_id) or {}
            base, inline_token = split_ws_url(cfg.get("ws_url", ""))
            token = cfg.get("access_token")
            if token is None or token == ACCESS_TOKEN_MASK or ACCESS_TOKEN_MASK in str(token):
                # 未提交 / 打码哨兵 → 保留旧值
                token = self._find_old_token(old, account_id, base) or inline_token
            else:
                token = str(token).strip()

            if "conn_mode" in cfg:
                mode = normalize_mode(cfg["conn_mode"])
            else:
                mode = normalize_mode(prev.get("conn_mode"))

            if "name" in cfg:
                name = normalize_account_name(cfg.get("name"), account_id)
            else:
                name = prev.get("name") or account_id

            normalized[account_id] = {
                "id": account_id,
                "name": name,
                "ws_url": join_ws_url(base, token),
                "owner_id": cfg.get("owner_id", prev.get("owner_id")),
                "auto_connect": bool(cfg.get("auto_connect", prev.get("auto_connect", False))),
                "conn_mode": mode,
            }

        sqls: list[tuple[str, Sequence[Any]]] = []
        # 只删「本次 payload 里没有的」id：id 稳定后不必像过去那样整体重建
        for account_id in set(old) - set(normalized):
            sqls.append(("DELETE FROM bots WHERE account_id = ?", (account_id,)))
            sqls.append(("DELETE FROM bot_accounts WHERE account_id = ?", (account_id,)))
        for order, (account_id, cfg) in enumerate(normalized.items()):
            sqls.append((
                "INSERT OR REPLACE INTO bots "
                "(account_id, name, ws_url, owner_id, auto_connect, conn_mode, sort_order) "
                "VALUES (?,?,?,?,?,?,?)",
                (account_id, cfg["name"], cfg["ws_url"], cfg.get("owner_id"),
                 1 if cfg.get("auto_connect") else 0, cfg.get("conn_mode", FORWARD), order),
            ))
        await self.db.run_in_transaction(sqls)   # 先落库，成功后再更新内存缓存
        removed = set(old) - set(normalized)
        for account_id in removed:
            self._bot_accounts.pop(account_id, None)
        self._bots = normalized
        self._publish_account_labels()
        await self._notify("bots", self.get_bots())

    @staticmethod
    def _find_old_token(old: dict[str, dict], account_id: str, base: str) -> str:
        """在旧配置中找应保留的 access_token：同 id 优先，其次按基础地址匹配。"""
        prev = old.get(account_id)
        if prev:
            _, token = split_ws_url(prev.get("ws_url", ""))
            if token:
                return token
        for item in old.values():
            old_base, token = split_ws_url(item.get("ws_url", ""))
            if old_base == base and token:
                return token
        return ""

    async def add_bot(self, cfg: dict) -> str:
        """新增账号，返回它的 id。

        ``cfg`` 至少要带 ``id``（缺省则自动分配 ``botN``）与可选 ``name``。

        「没填」与「填错了」必须分开处理：没填 → 自动分配；**填了但不合法 → 直接报错**。
        否则用户手滑打成 ``bad id!`` 会被静默换成另一个 id，之后再也对不上自己的脚本。
        id 已被占用时同样报错——id 是外部引用，绝不静默复用。
        """
        raw_id = cfg.get("id")
        raw_id = "" if raw_id is None else str(raw_id).strip()
        if raw_id:
            account_id = normalize_account_id(raw_id)
            if not account_id:
                raise ValueError(
                    f"账号 ID 不合法：{raw_id!r}"
                    "（只能用字母、数字、下划线、连字符，以字母或数字开头，1-32 个字符）"
                )
        else:
            account_id = suggest_account_id(self._bots)
        if account_id in self._bots:
            raise ValueError(f"账号 id 已存在：{account_id}")

        name = normalize_account_name(cfg.get("name"), f"账号 {len(self._bots) + 1}")
        bots = self.get_bots()
        bots.append({
            "id": account_id,
            "name": name,
            "ws_url": cfg.get("ws_url", ""),
            "owner_id": cfg.get("owner_id"),
            "auto_connect": bool(cfg.get("auto_connect", False)),
            # 新增账号默认正向：`conn_mode` 未指定时不能写成空值，否则 save_bots 只能靠
            # 「缺字段沿用旧值」兜底，而新账号没有旧值。
            "conn_mode": normalize_mode(cfg.get("conn_mode")),
        })
        await self.save_bots(bots)
        return account_id

    async def delete_bot(self, account_id: str) -> bool:
        if str(account_id) not in self._bots:
            return False
        bots = [b for b in self.get_bots() if str(b["id"]) != str(account_id)]
        await self.save_bots(bots)   # 连带清掉 bot_accounts 快照
        return True

    # ==================== 上次登录账号快照 ====================
    def get_bot_accounts(self) -> dict[str, dict]:
        """返回全部账号的「上次登录账号」快照（深拷贝，读方安全）。"""
        import copy

        return {aid: copy.deepcopy(info) for aid, info in self._bot_accounts.items()}

    def get_bot_account(self, account_id: str) -> dict | None:
        info = self._bot_accounts.get(str(account_id))
        return dict(info) if info else None

    async def save_bot_account(self, account_id: str, user_id: Any, nickname: str = "") -> bool:
        """记录某账号本次成功登录的 QQ。

        与库中快照相同（user_id 一致）时直接返回 False，不写库、不通知
        —— 满足「账号相同不变、不同才换」；返回 True 表示账号确实发生了变化。
        """
        try:
            normalized = str(user_id) if user_id is not None else ""
        except Exception as e:  # noqa: BLE001 —— 非法 user_id 一律按「没登录」处理
            self.log.debug(f"[Config] 账号 {account_id} 的 user_id 无法转换为字符串（按未登录处理）: {e}")
            normalized = ""
        if not normalized or normalized == "0":
            return False

        key = str(account_id)
        old = self._bot_accounts.get(key)
        if old and str(old.get("user_id")) == normalized:
            return False

        info = {
            "user_id": normalized,
            "nickname": str(nickname or ""),
            "last_login_at": int(time.time()),
        }
        await self.db.execute(
            "INSERT OR REPLACE INTO bot_accounts (account_id, user_id, nickname, last_login_at) "
            "VALUES (?,?,?,?)",
            (key, info["user_id"], info["nickname"], info["last_login_at"]),
        )
        self._bot_accounts[key] = info
        await self._notify("bot_accounts", {"account_id": key, **info})
        return True
