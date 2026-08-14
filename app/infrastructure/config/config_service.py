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
        """对外使用（WebUI/页面）：ws_url 中的 access_token 打码。"""
        return [{**dict(b), "ws_url": mask_ws_url(b.get("ws_url", ""))} for b in self._bots]

    async def save_bots(self, bots: list[dict]) -> None:
        """保存账号配置。若提交的 ws_url 是打码值，用当前存储的真实 access_token 回填。"""
        old = self._bots
        normalized = []
        for i, cfg in enumerate(bots):
            ws = cfg.get("ws_url", "")
            if i < len(old) and old[i].get("ws_url"):
                ws = restore_ws_url(ws, old[i]["ws_url"])
            normalized.append({**cfg, "ws_url": ws})
        self._bots = normalized
        sqls = [("DELETE FROM bots", ())]
        for i, cfg in enumerate(normalized):
            sqls.append((
                "INSERT OR REPLACE INTO bots (bot_index, ws_url, owner_id, auto_connect) VALUES (?,?,?,?)",
                (i, cfg.get("ws_url", ""), cfg.get("owner_id"), 1 if cfg.get("auto_connect") else 0),
            ))
        await self.db.run_in_transaction(sqls)
        await self._notify("bots", self._bots)

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
        self._webui = merged
        sqls = [("DELETE FROM webui_config", ())]
        for key, value in merged.items():
            sqls.append(("INSERT OR REPLACE INTO webui_config (key, value_json) VALUES (?,?)",
                         (key, self.db.dumps(value))))
        await self.db.run_in_transaction(sqls)
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
