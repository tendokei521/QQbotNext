"""配置中心：所有配置的单一入口。

- SQLite 落盘 + 内存缓存 + 变更通知（替代原 watchdog 文件监听）；
- 首次启动自动从旧 JSON 文件（webserver/webconfig.json、webui/webui_config.json、
  module/configs/*/config.json、module/configs/*/authority.json）迁移。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.core.logger import logger
from app.infrastructure.persistence.database import Database

Listener = Callable[[str, Any], Awaitable[None]]

DEFAULT_WEBUI_CONFIG: dict = {
    "logs": {"visible_levels": ["info", "warning", "error"], "max_lines": 50, "console_height": 200},
    "single_service": {},
    "multi_group": {"show_all": False, "groups": {}},
}

# OneBot ws_url 中 access_token 的对外打码哨兵（WebUI/页面不泄露真实令牌）
ACCESS_TOKEN_MASK = "****"


def mask_ws_url(url: str) -> str:
    """把 ws_url 中的 access_token 打码，避免 WebUI / 页面 HTML 泄露令牌。"""
    if not url or "access_token=" not in url:
        return url
    import re

    return re.sub(r"(access_token=)[^&]*", r"\g<1>" + ACCESS_TOKEN_MASK, url)


def split_ws_url(url: str) -> tuple[str, str]:
    """把完整 ws_url 拆成 (基础地址, access_token)。

    仅拆出 access_token 参数，query 中其余参数保留在基础地址里。
    """
    if not url or "?" not in url:
        return (url or ""), ""
    base, query = url.split("?", 1)
    token = ""
    kept = []
    for param in query.split("&"):
        if param.startswith("access_token="):
            token = param[len("access_token="):]
        elif param:
            kept.append(param)
    if kept:
        return f"{base}?{'&'.join(kept)}", token
    return base, token


def join_ws_url(base: str, token: str) -> str:
    """把基础地址与 access_token 拼回完整 ws_url（无 token 时原样返回）。"""
    base = (base or "").strip()
    if not token:
        return base
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}access_token={token}"


def restore_ws_url(masked: str, original: str) -> str:
    """保存时：提交的是打码 URL → 用原 URL 的真实 access_token 回填。"""
    if not masked or ACCESS_TOKEN_MASK not in masked or not original:
        return masked
    import re

    match = re.search(r"access_token=([^&]*)", original)
    if not match:
        return masked
    return re.sub(r"access_token=" + re.escape(ACCESS_TOKEN_MASK), f"access_token={match.group(1)}", masked)


class ConfigService:
    def __init__(self, db: Database, project_root: Path, log=None) -> None:
        self.db = db
        self.root = project_root
        self.log = log or logger
        self._listeners: list[Listener] = []

        # 内存缓存（source of truth for 读取）
        self._bots: list[dict] = []
        self._webui: dict = dict(DEFAULT_WEBUI_CONFIG)
        self._module_config: dict[str, dict[str, dict]] = {}      # module -> {bot_id: config}
        self._module_authority: dict[str, dict[str, dict]] = {}   # module -> {bot_id: authority}

    # ==================== 生命周期 ====================
    async def init(self) -> None:
        await self._migrate_legacy_if_needed()
        await self._load_all()

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
                    await self.db.execute(
                        "INSERT OR REPLACE INTO bots (bot_index, ws_url, owner_id, auto_connect) VALUES (?,?,?,?)",
                        (i, cfg.get("ws_url", ""), cfg.get("owner_id"), 1 if cfg.get("auto_connect") else 0),
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
        # bots
        rows = await self.db.fetchall("SELECT * FROM bots ORDER BY bot_index")
        self._bots = []
        for r in rows:
            owner_id = r["owner_id"]
            if owner_id is not None:
                try:
                    owner_id = int(owner_id)
                except (TypeError, ValueError):
                    pass
            self._bots.append({
                "ws_url": r["ws_url"],
                "owner_id": owner_id,
                "auto_connect": bool(r["auto_connect"]),
            })

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

        self.log.debug(
            f"[Config] 加载完成: bots={len(self._bots)} modules={len(self._module_config)}"
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
        """内部使用：返回真实 ws_url（网关/连接）。"""
        return [dict(b) for b in self._bots]

    def get_bots_public(self) -> list[dict]:
        """对外使用（WebUI 配置接口）：ws_url 拆出基础地址，access_token 独立字段回显真实值。

        配置页需要直观查看/编辑 token，因此不在这里打码——安全由接口鉴权
        （WEBUI_TOKEN）与传输层（HTTPS 反代）保证；页面 HTML 渲染走
        gateway.get_bots_info()（纯地址，不含 token）。
        """
        result = []
        for index, b in enumerate(self._bots):
            base, token = split_ws_url(b.get("ws_url", ""))
            result.append({
                **{k: v for k, v in dict(b).items() if k != "access_token"},
                "index": index,
                "ws_url": base,
                "access_token": token,
            })
        return result

    async def save_bots(self, bots: list[dict]) -> None:
        """保存账号配置。支持独立 access_token 字段（WebUI 独立输入框）：

        - access_token 字段缺失 或 值为打码哨兵 → 保留旧 token（位置优先、基础地址匹配兜底）；
        - access_token 为显式值（含空串）→ 直接采用（空串 = 清除 token）；
        - 兼容旧前端：ws_url 本身携带打码 access_token 时同样回填。
        """
        old = self._bots
        normalized = []
        for i, cfg in enumerate(bots):
            base, inline_token = split_ws_url(cfg.get("ws_url", ""))
            token = cfg.get("access_token")
            if token is None or token == ACCESS_TOKEN_MASK or ACCESS_TOKEN_MASK in str(token):
                # 未提交 / 打码哨兵 → 保留旧值
                token = self._find_old_token(old, i, base) or inline_token
            else:
                token = str(token).strip()
            clean = {**cfg, "ws_url": join_ws_url(base, token)}
            clean.pop("access_token", None)  # 独立字段不落库（存储仍为单字段 ws_url）
            normalized.append(clean)
        sqls = [("DELETE FROM bots", ())]
        for i, cfg in enumerate(normalized):
            sqls.append((
                "INSERT OR REPLACE INTO bots (bot_index, ws_url, owner_id, auto_connect) VALUES (?,?,?,?)",
                (i, cfg.get("ws_url", ""), cfg.get("owner_id"), 1 if cfg.get("auto_connect") else 0),
            ))
        await self.db.run_in_transaction(sqls)   # 先落库，成功后再更新内存缓存
        self._bots = normalized
        await self._notify("bots", self._bots)

    @staticmethod
    def _find_old_token(old: list[dict], index: int, base: str) -> str:
        """在旧配置中找应保留的 access_token：位置优先，其次按基础地址特征匹配。"""
        if index < len(old):
            _, token = split_ws_url(old[index].get("ws_url", ""))
            if token:
                return token
        for item in old:
            old_base, token = split_ws_url(item.get("ws_url", ""))
            if old_base == base and token:
                return token
        return ""

    async def add_bot(self, cfg: dict) -> int:
        self._bots.append(dict(cfg))
        await self.save_bots(self._bots)
        return len(self._bots) - 1

    async def delete_bot(self, index: int) -> bool:
        if index < 0 or index >= len(self._bots):
            return False
        self._bots.pop(index)
        await self.save_bots(self._bots)
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
        await self._notify("module_config", {"module": module, "bot_id": bot_id, "config": dict(config)})

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
        await self._notify("authority", {"module": module, "bot_id": bot_id, "authority": dict(authority)})

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

    def _schedule_persist(self, persist_fn: Callable[..., Any], *args: Any) -> None:
        try:
            asyncio.get_running_loop().create_task(persist_fn(*args))
        except RuntimeError:
            pass  # 无运行中的事件循环（测试环境）：仅内存生效
