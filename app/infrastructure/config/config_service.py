"""配置中心：所有配置的单一入口。

- SQLite 落盘 + 内存缓存 + 变更通知（替代原 watchdog 文件监听）；
- 首次启动自动从旧 JSON 文件（webserver/webconfig.json、webui/webui_config.json、
  module/configs/*/config.json、module/configs/*/authority.json）迁移。
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

from app.core.logger import logger, set_account_labels
from app.infrastructure.config.ws_url import (
    ACCESS_TOKEN_MASK,
    join_ws_url,
    mask_ws_url,
    restore_ws_url,
    split_ws_url,
)
from app.infrastructure.onebot.endpoint import FORWARD, normalize_mode
from app.infrastructure.persistence.account_migration import (
    migrate_account_identity,
    normalize_account_id,
    normalize_account_name,
    suggest_account_id,
)

#: 这四个名字历史上就在这里定义，外部（gateway / webui）从本模块导入。
#: 现在实现搬到了 `ws_url.py`，这里保留重导出；`__all__` 让 linter 知道它们是公共 API。
__all__ = [
    "ACCESS_TOKEN_MASK",
    "ConfigService",
    "mask_ws_url",
    "join_ws_url",
    "restore_ws_url",
    "split_ws_url",
]
from app.infrastructure.persistence.database import Database

Listener = Callable[[str, Any], Awaitable[None]]

DEFAULT_WEBUI_CONFIG: dict = {
    "logs": {"show_raw_logs": False, "visible_levels": ["info", "warning", "error"], "max_lines": 50, "console_height": 200},
    "single_service": {},
    "multi_group": {"show_all": False, "groups": {}},
    "experimental": {"show_experimental": False},
}

DEFAULT_PROVIDER_SETTINGS: dict = {
    "default_preset_id": "",
    "default_model_id": "",
    "fallback_model_ids": [],
    "provider_pool": ["*"],
}

# OneBot ws_url 中 access_token 的对外打码哨兵（WebUI/页面不泄露真实令牌）


class ConfigService:
    def __init__(self, db: Database, project_root: Path, log=None) -> None:
        self.db = db
        self.root = project_root
        self.log = log or logger
        self._listeners: list[Listener] = []

        # 内存缓存（source of truth for 读取）
        # 账号：account_id -> {id, name, ws_url, owner_id, auto_connect, conn_mode}
        # 用 dict 保序（Python 3.7+ 插入序 = 界面顺序），排序另存 sort_order 供重启后还原。
        self._bots: dict[str, dict] = {}
        # 每个账号上一次成功登录的账号快照：{"user_id": str, "nickname": str, "last_login_at": int}
        # 断开后运行时状态会清空，前端据此回退展示；连接成功时由 gateway 回调刷新。
        self._bot_accounts: dict[str, dict] = {}
        self._webui: dict = dict(DEFAULT_WEBUI_CONFIG)
        self._module_config: dict[str, dict[str, dict]] = {}      # module -> {bot_id: config}
        self._module_authority: dict[str, dict[str, dict]] = {}   # module -> {bot_id: authority}
        self._provider_presets: dict[str, dict] = {}              # preset_id -> preset
        self._provider_models: dict[str, dict] = {}               # model_id -> model
        self._provider_settings: dict = dict(DEFAULT_PROVIDER_SETTINGS)
        self._role_presets: dict[str, dict] = {}                  # role_id -> role preset

    # ==================== 生命周期 ====================
    async def init(self) -> None:
        # 账号身份迁移必须最先跑：把旧库的 bot_index 身份换成 account_id，
        # 之后所有读取（_load_all / 网关 / API）才有一致的 id 可用。
        await migrate_account_identity(self.db)
        await self._ensure_schema()
        await self._migrate_legacy_if_needed()
        await self._load_all()

    async def _ensure_schema(self) -> None:
        """老库补列：`conn_mode`（连接方向）晚于 `bots` 表引入。

        存量库的 `bots` 表只有 ws_url/owner_id/auto_connect；不补列会让
        `SELECT *` 之后所有读取都拿不到连接模式，反向账号被静默当成正向去连。
        默认值 `forward` 保证升级后存量账号行为不变。
        """
        await self.db.ensure_columns("bots", {"conn_mode": "TEXT NOT NULL DEFAULT 'forward'"})

    async def _migrate_legacy_if_needed(self) -> None:
        count = await self.db.fetchone("SELECT COUNT(*) AS c FROM module_config")
        if count and count["c"] > 0:
            return  # 已迁移
        await self._migrate_legacy()

    async def _migrate_legacy(self) -> None:
        """从旧 JSON 文件迁移数据（幂等：仅当 DB 为空时执行）。"""
        migrated = []

        # 1. bots
        bots_file = self.root / "webserver" / "webconfig.json"
        if bots_file.exists():
            try:
                data = json.loads(bots_file.read_text(encoding="utf-8"))
                bots = data.get("bots", [])
                for i, cfg in enumerate(bots):
                    account_id = f"bot{i + 1}"
                    await self.db.execute(
                        "INSERT OR REPLACE INTO bots "
                        "(account_id, name, ws_url, owner_id, auto_connect, sort_order) "
                        "VALUES (?,?,?,?,?,?)",
                        (account_id, f"账号 {i + 1}", cfg.get("ws_url", ""), cfg.get("owner_id"),
                         1 if cfg.get("auto_connect") else 0, i),
                    )
                migrated.append(f"bots({len(bots)})")
            except Exception as e:
                self.log.error(f"[Config] 迁移 bots 失败: {e}")

        # 2. webui 配置
        webui_file = self.root / "webui" / "webui_config.json"
        if webui_file.exists():
            try:
                data = json.loads(webui_file.read_text(encoding="utf-8"))
                for key, value in data.items():
                    await self.db.execute(
                        "INSERT OR REPLACE INTO webui_config (key, value_json) VALUES (?,?)",
                        (key, self.db.dumps(value)),
                    )
                migrated.append("webui")
            except Exception as e:
                self.log.error(f"[Config] 迁移 webui 配置失败: {e}")

        # 3. 模块配置 / 权限（module/configs/<name>/{config.json,authority.json}）
        configs_dir = self.root / "module" / "configs"
        if configs_dir.exists():
            for cfg_dir in sorted(configs_dir.iterdir()):
                if not cfg_dir.is_dir() or cfg_dir.name.startswith(("_", ".")):
                    continue
                cfg_file = cfg_dir / "config.json"
                auth_file = cfg_dir / "authority.json"
                if cfg_file.exists():
                    await self._migrate_module_json(cfg_dir.name, "config", cfg_file)
                if auth_file.exists():
                    await self._migrate_module_json(cfg_dir.name, "authority", auth_file)

        if migrated:
            self.log.info(f"[Config] 已从旧 JSON 迁移: {', '.join(migrated)}")

    async def _migrate_module_json(self, module: str, kind: str, path: Path) -> None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        if not isinstance(data, dict):
            return
        items = data.items()
        for bot_id, value in items:
            if kind == "config":
                if isinstance(value, dict):
                    await self.db.execute(
                        "INSERT OR REPLACE INTO module_config (module_name, bot_id, config_json, updated_at) VALUES (?,?,?,?)",
                        (module, bot_id, self.db.dumps(value), int(asyncio.get_running_loop().time())),
                    )
            else:  # authority
                if isinstance(value, dict):
                    await self.db.execute(
                        "INSERT OR REPLACE INTO module_authority "
                        "(module_name, bot_id, enabled, group_mode, group_list, user_mode, user_list, updated_at) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (
                            module, bot_id,
                            1 if value.get("enabled", True) else 0,
                            value.get("group_mode", "blacklist"),
                            self.db.dumps(value.get("group_list", [])),
                            value.get("user_mode", "blacklist"),
                            self.db.dumps(value.get("user_list", [])),
                            int(asyncio.get_running_loop().time()),
                        ),
                    )

    async def _load_all(self) -> None:
        # bots（按 sort_order 还原界面顺序；id 是唯一凭据，顺序只是展示）
        rows = await self.db.fetchall("SELECT * FROM bots ORDER BY sort_order, account_id")
        self._bots = {}
        for r in rows:
            owner_id = r["owner_id"]
            if owner_id is not None:
                try:
                    owner_id = int(owner_id)
                except (TypeError, ValueError):
                    pass
            account_id = str(r["account_id"])
            self._bots[account_id] = {
                "id": account_id,
                "name": normalize_account_name(r["name"], account_id),
                "ws_url": r["ws_url"],
                "owner_id": owner_id,
                "auto_connect": bool(r["auto_connect"]),
                "conn_mode": normalize_mode(r.get("conn_mode")),
            }

        # 上次登录账号快照（老库无此表时由 SCHEMA 的 CREATE TABLE IF NOT EXISTS 补建）
        rows = await self.db.fetchall("SELECT * FROM bot_accounts")
        self._bot_accounts = {}
        for r in rows:
            self._bot_accounts[str(r["account_id"])] = {
                "user_id": r["user_id"],
                "nickname": r["nickname"] or "",
                "last_login_at": int(r["last_login_at"] or 0),
            }

        # webui
        rows = await self.db.fetchall("SELECT * FROM webui_config")
        for r in rows:
            self._webui[r["key"]] = self.db.loads(r["value_json"], {})

        # module config
        rows = await self.db.fetchall("SELECT * FROM module_config")
        for r in rows:
            self._module_config.setdefault(r["module_name"], {})[r["bot_id"]] = self.db.loads(r["config_json"], {})

        # module authority
        rows = await self.db.fetchall("SELECT * FROM module_authority")
        for r in rows:
            self._module_authority.setdefault(r["module_name"], {})[r["bot_id"]] = {
                "enabled": bool(r["enabled"]),
                "group_mode": r["group_mode"],
                "group_list": self.db.loads(r["group_list"], []),
                "user_mode": r["user_mode"],
                "user_list": self.db.loads(r["user_list"], []),
            }

        # provider presets
        rows = await self.db.fetchall("SELECT * FROM provider_presets")
        for r in rows:
            self._provider_presets[r["id"]] = {
                "id": r["id"],
                "name": r["name"],
                "provider": r["provider"],
                "config": self.db.loads(r["config_json"], {}),
                "enabled": bool(r["enabled"]),
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }

        # provider models
        rows = await self.db.fetchall("SELECT * FROM provider_models")
        for r in rows:
            self._provider_models[r["id"]] = {
                "id": r["id"],
                "preset_id": r["preset_id"],
                "model": r["model"],
                "provider_type": r["provider_type"],
                "enabled": bool(r["enabled"]),
                "config": self.db.loads(r["config_json"], {}),
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }

        # provider settings
        rows = await self.db.fetchall("SELECT * FROM provider_settings")
        for r in rows:
            self._provider_settings[r["key"]] = self.db.loads(r["value_json"], None)

        # role presets
        rows = await self.db.fetchall("SELECT * FROM role_presets")
        for r in rows:
            self._role_presets[r["id"]] = {
                "id": r["id"],
                "name": r["name"],
                "description": r["description"],
                "system_prompt": r["system_prompt"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }

        self._publish_account_labels()
        self.log.debug(
            f"[Config] 加载完成: bots={len(self._bots)} modules={len(self._module_config)} "
            f"presets={len(self._provider_presets)} models={len(self._provider_models)} roles={len(self._role_presets)}"
        )

    # ==================== 变更通知 ====================
    def on_change(self, listener: Listener) -> None:
        self._listeners.append(listener)

    async def _notify(self, scope: str, payload: Any = None) -> None:
        for listener in list(self._listeners):
            try:
                await listener(scope, payload)
            except Exception as e:
                self.log.error(f"[Config] 变更通知失败: {e}")

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
        except Exception:
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

    # ==================== webui ====================
    def get_webui_config(self) -> dict:
        """返回深拷贝，防止调用方原地污染内存缓存。"""
        import copy

        return copy.deepcopy(self._webui)

    async def save_webui_config(self, config: dict) -> None:
        merged = {**DEFAULT_WEBUI_CONFIG, **config}
        sqls = [("DELETE FROM webui_config", ())]
        for key, value in merged.items():
            sqls.append(("INSERT OR REPLACE INTO webui_config (key, value_json) VALUES (?,?)",
                         (key, self.db.dumps(value))))
        await self.db.run_in_transaction(sqls)   # 先落库，成功后再更新内存缓存
        self._webui = merged
        await self._notify("webui", merged)

    # ==================== 模块配置（同步读取，异步持久化） ====================
    def get_module_config(self, module: str, bot_id: Any) -> dict:
        data = self._module_config.get(module, {})
        return dict(data.get(str(bot_id)) or data.get(bot_id) or {})

    def get_all_module_configs(self, module: str) -> dict[str, dict]:
        return dict(self._module_config.get(module, {}))

    def set_module_config(self, module: str, bot_id: Any, config: dict, persist: bool = True) -> None:
        key = str(bot_id) if bot_id is not None else None
        self._module_config.setdefault(module, {})[key] = dict(config)
        if persist:
            self._schedule_persist(self._persist_module_config, module, key)

    async def save_module_config(self, module: str, bot_id: Any, config: dict) -> None:
        key = str(bot_id) if bot_id is not None else None
        self._module_config.setdefault(module, {})[key] = dict(config)
        await self._persist_module_config(module, key)
        await self._notify("module_config", {"module": module, "account_id": bot_id, "config": dict(config)})

    async def _persist_module_config(self, module: str, key: Any) -> None:
        try:
            config = self._module_config.get(module, {}).get(key, {})
            await self.db.execute(
                "INSERT OR REPLACE INTO module_config (module_name, bot_id, config_json, updated_at) VALUES (?,?,?,?)",
                (module, key, self.db.dumps(config), int(asyncio.get_running_loop().time())),
            )
        except Exception as e:
            self.log.error(f"[Config] 持久化模块配置失败 {module}/{key}: {e}")

    # ==================== 模块权限 ====================
    def get_module_authority(self, module: str, bot_id: Any) -> dict:
        data = self._module_authority.get(module, {})
        return dict(data.get(str(bot_id)) or data.get(bot_id) or {})

    def get_default_authority(self, module: str) -> dict:
        return dict(self._module_authority.get(module, {}).get(None, {
            "enabled": True,
            "group_mode": "blacklist",
            "group_list": [],
            "user_mode": "blacklist",
            "user_list": [],
        }))

    def set_module_authority(self, module: str, bot_id: Any, authority: dict) -> None:
        key = str(bot_id) if bot_id is not None else None
        self._module_authority.setdefault(module, {})[key] = dict(authority)
        self._schedule_persist(self._persist_module_authority, module, key)

    async def save_module_authority(self, module: str, bot_id: Any, authority: dict) -> None:
        key = str(bot_id) if bot_id is not None else None
        self._module_authority.setdefault(module, {})[key] = dict(authority)
        await self._persist_module_authority(module, key)
        await self._notify("authority", {"module": module, "account_id": bot_id, "authority": dict(authority)})

    async def _persist_module_authority(self, module: str, key: Any) -> None:
        try:
            auth = self._module_authority.get(module, {}).get(key, {})
            await self.db.execute(
                "INSERT OR REPLACE INTO module_authority "
                "(module_name, bot_id, enabled, group_mode, group_list, user_mode, user_list, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    module, key,
                    1 if auth.get("enabled", True) else 0,
                    auth.get("group_mode", "blacklist"),
                    self.db.dumps(auth.get("group_list", [])),
                    auth.get("user_mode", "blacklist"),
                    self.db.dumps(auth.get("user_list", [])),
                    int(asyncio.get_running_loop().time()),
                ),
            )
        except Exception as e:
            self.log.error(f"[Config] 持久化权限失败 {module}/{key}: {e}")

    # ==================== Provider 预设（全局共享连接配置） ====================
    def list_provider_presets(self) -> list[dict]:
        """返回全部 Provider 预设（深拷贝，调用方可安全修改）。"""
        import copy

        return [copy.deepcopy(self._provider_presets[k]) for k in sorted(self._provider_presets)]

    def get_provider_preset(self, preset_id: str) -> dict | None:
        """按 ID 返回 Provider 预设（深拷贝），不存在返回 None。"""
        import copy

        preset = self._provider_presets.get(str(preset_id))
        return copy.deepcopy(preset) if preset else None

    def upsert_provider_preset(self, preset_id: str, preset: dict) -> None:
        """更新内存缓存并异步落盘（无事件循环时仅内存生效）。"""
        key = str(preset_id)
        self._provider_presets[key] = dict(preset)
        self._schedule_persist(self._persist_provider_preset, key)

    async def save_provider_preset(self, preset_id: str, preset: dict) -> None:
        """持久化 Provider 预设并广播变更。"""
        key = str(preset_id)
        self._provider_presets[key] = dict(preset)
        await self._persist_provider_preset(key)
        await self._notify("provider_presets", self.list_provider_presets())

    async def delete_provider_preset(self, preset_id: str) -> bool:
        """删除 Provider 预设并广播变更。"""
        key = str(preset_id)
        if key not in self._provider_presets:
            return False
        del self._provider_presets[key]
        try:
            await self.db.execute("DELETE FROM provider_presets WHERE id = ?", (key,))
        except Exception as e:
            self.log.error(f"[Config] 删除 Provider 预设失败 {key}: {e}")
            return False
        await self._notify("provider_presets", self.list_provider_presets())
        return True

    async def _persist_provider_preset(self, preset_id: str) -> None:
        try:
            preset = self._provider_presets.get(preset_id)
            if preset is None:
                await self.db.execute("DELETE FROM provider_presets WHERE id = ?", (preset_id,))
                return
            await self.db.execute(
                "INSERT OR REPLACE INTO provider_presets "
                "(id, name, provider, config_json, enabled, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    preset_id,
                    preset.get("name", ""),
                    preset.get("provider", "openai"),
                    self.db.dumps(preset.get("config", {})),
                    1 if preset.get("enabled", True) else 0,
                    int(preset.get("created_at", 0)),
                    int(preset.get("updated_at", 0)),
                ),
            )
        except Exception as e:
            self.log.error(f"[Config] 持久化 Provider 预设失败 {preset_id}: {e}")

    # ==================== Provider 模型（连接预设下的模型实例） ====================
    def list_provider_models(self, preset_id: str | None = None) -> list[dict]:
        """返回模型实例列表（深拷贝），可按预设过滤。"""
        import copy

        models = [copy.deepcopy(self._provider_models[k]) for k in sorted(self._provider_models)]
        if preset_id:
            models = [m for m in models if m.get("preset_id") == preset_id]
        return models

    def get_provider_model(self, model_id: str) -> dict | None:
        import copy

        model = self._provider_models.get(str(model_id))
        return copy.deepcopy(model) if model else None

    async def save_provider_model(self, model_id: str, model: dict) -> None:
        """持久化模型实例并广播变更。"""
        key = str(model_id)
        self._provider_models[key] = dict(model)
        await self._persist_provider_model(key)
        await self._notify("provider_models", self.list_provider_models())

    async def delete_provider_model(self, model_id: str) -> bool:
        key = str(model_id)
        if key not in self._provider_models:
            return False
        del self._provider_models[key]
        try:
            await self.db.execute("DELETE FROM provider_models WHERE id = ?", (key,))
        except Exception as e:
            self.log.error(f"[Config] 删除 Provider 模型失败 {key}: {e}")
            return False
        await self._notify("provider_models", self.list_provider_models())
        return True

    async def _persist_provider_model(self, model_id: str) -> None:
        try:
            model = self._provider_models.get(model_id)
            if model is None:
                await self.db.execute("DELETE FROM provider_models WHERE id = ?", (model_id,))
                return
            await self.db.execute(
                "INSERT OR REPLACE INTO provider_models "
                "(id, preset_id, model, provider_type, enabled, config_json, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    model_id,
                    model.get("preset_id", ""),
                    model.get("model", ""),
                    model.get("provider_type", "chat"),
                    1 if model.get("enabled", True) else 0,
                    self.db.dumps(model.get("config", {})),
                    int(model.get("created_at", 0)),
                    int(model.get("updated_at", 0)),
                ),
            )
        except Exception as e:
            self.log.error(f"[Config] 持久化 Provider 模型失败 {model_id}: {e}")

    # ==================== Provider 全局设置 ====================
    def get_provider_settings(self) -> dict:
        import copy

        return {**DEFAULT_PROVIDER_SETTINGS, **copy.deepcopy(self._provider_settings)}

    async def save_provider_settings(self, settings: dict) -> None:
        merged = {**DEFAULT_PROVIDER_SETTINGS, **(settings or {})}
        self._provider_settings = dict(merged)
        for key, value in merged.items():
            await self.db.execute(
                "INSERT OR REPLACE INTO provider_settings (key, value_json) VALUES (?,?)",
                (key, self.db.dumps(value)),
            )
        await self._notify("provider_settings", self.get_provider_settings())

    # ==================== 角色预设 ====================
    def list_role_presets(self) -> list[dict]:
        """返回全部角色预设（深拷贝，调用方可安全修改）。"""
        import copy

        return [copy.deepcopy(self._role_presets[k]) for k in sorted(self._role_presets)]

    def get_role_preset(self, role_id: str) -> dict | None:
        """按 ID 返回角色预设（深拷贝），不存在返回 None。"""
        import copy

        preset = self._role_presets.get(str(role_id))
        return copy.deepcopy(preset) if preset else None

    async def save_role_preset(self, role_id: str, preset: dict) -> None:
        key = str(role_id)
        self._role_presets[key] = dict(preset)
        await self._persist_role_preset(key)
        await self._notify("role_presets", self.list_role_presets())

    async def delete_role_preset(self, role_id: str) -> bool:
        key = str(role_id)
        if key not in self._role_presets:
            return False
        del self._role_presets[key]
        try:
            await self.db.execute("DELETE FROM role_presets WHERE id = ?", (key,))
        except Exception as e:
            self.log.error(f"[Config] 删除角色预设失败 {key}: {e}")
            return False
        await self._notify("role_presets", self.list_role_presets())
        return True

    async def _persist_role_preset(self, role_id: str) -> None:
        try:
            preset = self._role_presets.get(role_id)
            if preset is None:
                await self.db.execute("DELETE FROM role_presets WHERE id = ?", (role_id,))
                return
            await self.db.execute(
                "INSERT OR REPLACE INTO role_presets "
                "(id, name, description, system_prompt, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?)",
                (
                    role_id,
                    preset.get("name", ""),
                    preset.get("description", ""),
                    preset.get("system_prompt", ""),
                    int(preset.get("created_at", 0)),
                    int(preset.get("updated_at", 0)),
                ),
            )
        except Exception as e:
            self.log.error(f"[Config] 持久化角色预设失败 {role_id}: {e}")

    def _schedule_persist(self, persist_fn: Callable[..., Any], *args: Any) -> None:
        try:
            asyncio.get_running_loop().create_task(persist_fn(*args))
        except RuntimeError:
            pass  # 无运行中的事件循环（测试环境）：仅内存生效
