"""就绪诊断：回答"现在能不能回话"，以及"差哪一步"。

## 为什么需要

判断"机器人能不能用"要同时看账号、连接、模型池、场景开关、协议端监听、模块加载——
这些状态散在五六个地方，用户只能自己拼。拼错的表现就是"坏了"（群里 @ 没反应、
日志也干净）。

这里把它们聚合成一份 **checks**，三个消费者共用同一份数据：

1. 启动横幅（`bootstrap` 打印一次）；
2. WebUI 总览的"就绪卡"（`GET /api/readiness`，轮询）；
3. 首启向导的完成判定（所有 blocker 通过才算完成）。

每项都带 ``action``（该去哪修），保证"点一下就能修"。
"""

from __future__ import annotations

import sys
from typing import Any

from app.core.logger import logger

#: 检查项严重级别：blocker 会让 ready=False；warn 只提示；info 仅展示
LEVEL_BLOCKER = "blocker"
LEVEL_WARN = "warn"
LEVEL_INFO = "info"


def _check(
    check_id: str,
    ok: bool,
    title: str,
    *,
    level: str = LEVEL_BLOCKER,
    detail: str = "",
    route: str = "",
    label: str = "",
    account_id: str = "",
) -> dict:
    action = None
    if route:
        action = {"route": route, "label": label}
        if account_id:
            # 前端据此先切换到该账号再跳转：否则跳到的是"当前选中账号"，可能不是要修的那个
            action["account_id"] = str(account_id)
    return {
        "id": check_id,
        "ok": bool(ok),
        "level": level,
        "title": title,
        "detail": detail,
        "action": action,
    }


class ReadinessService:
    """聚合各运行状态。所有依赖都可空——缺哪个就跳过对应检查（不抛错）。"""

    def __init__(
        self,
        config_service: Any = None,
        gateway: Any = None,
        agent_manager: Any = None,
        module_registry: Any = None,
    ) -> None:
        self.config_service = config_service
        self.gateway = gateway
        self.agent_manager = agent_manager
        self.module_registry = module_registry

    # ---------- 各检查 ----------

    def _accounts(self) -> list[dict]:
        try:
            return list(self.config_service.get_bots() or [])
        except Exception as e:  # noqa: BLE001 —— 诊断接口不能因一个状态读失败而整体 500
            logger.add_info("Readiness").debug(f"[Readiness] 读账号失败: {e}")
            return []

    def _connections(self) -> dict:
        try:
            return dict(getattr(self.gateway, "connections", {}) or {})
        except Exception as e:  # noqa: BLE001
            logger.add_info("Readiness").debug(f"[Readiness] 读连接表失败: {e}")
            return {}

    def _runtimes(self) -> dict:
        try:
            return dict(self.agent_manager.runtimes() or {})
        except Exception as e:  # noqa: BLE001
            logger.add_info("Readiness").debug(f"[Readiness] 读运行时失败: {e}")
            return {}

    def _account_names(self) -> dict[str, str]:
        """账号 id → 展示名（就绪文案只写名字，id 是给程序判断用的）。"""
        names: dict[str, str] = {}
        for account in self._accounts():
            account_id = str(account.get("id") or "")
            if account_id:
                names[account_id] = str(account.get("name") or account_id)
        return names

    @staticmethod
    def _config_of(runtime) -> dict:
        raw = getattr(getattr(runtime, "config", None), "raw_config", {})
        return dict(raw) if isinstance(raw, dict) else {}

    @staticmethod
    def _pool_of(config: dict) -> list:
        pool = config.get("provider_model_pool")
        if isinstance(pool, list) and pool:
            return pool
        legacy = config.get("provider_model_id")
        return [legacy] if legacy else []

    def _module_errors(self) -> list[str]:
        registry = self.module_registry
        if registry is None:
            return []
        try:
            entries = registry.catalog_entries() if hasattr(registry, "catalog_entries") else []
        except Exception as e:  # noqa: BLE001
            logger.add_info("Readiness").debug(f"[Readiness] 读模块编目失败: {e}")
            return []
        errors: list[str] = []
        for entry in entries:
            status = str(getattr(entry, "status", "") or "")
            if status in ("error", "conflict"):
                errors.append(str(getattr(entry, "name", "?") or "?"))
        return errors

    # ---------- 汇总 ----------

    def snapshot(self) -> dict:
        checks: list[dict] = []

        checks.append(_check(
            "python", True, f"Python {sys.version.split()[0]}", level=LEVEL_INFO
        ))

        accounts = self._accounts()
        checks.append(_check(
            "account",
            bool(accounts),
            "已添加账号" if accounts else "还没有账号",
            detail=f"{len(accounts)} 个" if accounts else "",
            route="/bots",
            label="添加账号",
        ))

        names = self._account_names()
        missing_address = [
            str(account.get("id"))
            for account in accounts
            if not str(account.get("ws_url") or "").strip()
        ]
        if missing_address:
            # 正向「向外连接」允许先不填地址（等用户填协议端地址），
            # 但这时点"去连接"必然失败——提前用 warn 指出缺什么。
            checks.append(_check(
                "account_address",
                False,
                "账号还没填连接地址",
                level=LEVEL_WARN,
                detail="、".join(names.get(aid, aid) for aid in missing_address),
                route="/bots",
                label="去填写",
                account_id=missing_address[0],
            ))

        connections = self._connections()
        connected = [
            aid for aid, conn in connections.items()
            if getattr(conn, "status", "") == "connected"
        ]
        disconnected = [
            str(account.get("id")) for account in accounts
            if str(account.get("id")) not in {str(x) for x in connected}
        ]
        checks.append(_check(
            "connected",
            bool(connected),
            "所有账号已连接" if connected and not disconnected else "还有账号没连上",
            detail=(
                "、".join(names.get(aid, aid) for aid in disconnected)
                if disconnected else "连接成功后才能收发消息"
            ),
            route="/bots",
            label="去连接",
            account_id=disconnected[0] if disconnected else "",
        ))

        runtimes = self._runtimes()
        pools = {
            str(aid): self._pool_of(self._config_of(runtime))
            for aid, runtime in runtimes.items()
        }
        missing_pool = [aid for aid, pool in pools.items() if not pool]
        pool_ok = bool(pools) and not missing_pool
        checks.append(_check(
            "model_pool",
            pool_ok,
            "模型已选好" if pool_ok else "还没选模型",
            detail=(
                ("、".join(names.get(aid, aid) for aid in missing_pool))
                if missing_pool else "选好模型后机器人才能回复"
            ),
            route="/ai",
            label="选择模型",
            account_id=missing_pool[0] if missing_pool else "",
        ))

        scenes = {
            str(aid): bool(cfg.get("group_enable") or cfg.get("private_enable"))
            for aid, cfg in ((str(aid), self._config_of(rt)) for aid, rt in runtimes.items())
        }
        missing_scene = [aid for aid, enabled in scenes.items() if not enabled]
        scene_ok = bool(scenes) and not missing_scene
        checks.append(_check(
            "scene_enabled",
            scene_ok,
            "群聊或私聊已开启" if scene_ok else "群聊和私聊都没开启",
            detail=(
                ("、".join(names.get(aid, aid) for aid in missing_scene))
                if missing_scene else "群聊回复关闭时，群里 @ 机器人不会有任何回应"
            ),
            route="/ai",
            label="开启回复",
            account_id=missing_scene[0] if missing_scene else "",
        ))

        # 协议端监听/连接：反向账号看 listening，正向账号已被 connected 覆盖
        listening: list[str] = []
        for aid, conn in connections.items():
            try:
                if getattr(conn, "conn_mode", "") == "reverse" and self.gateway.reverse_link.listening(aid):
                    listening.append(str(aid))
            except Exception as e:  # noqa: BLE001
                logger.add_info("Readiness").debug(f"[Readiness] 读监听状态失败 {aid}: {e}")
                continue
        if listening:
            checks.append(_check(
                "protocol_listen", True, "已开始等待协议端接入",
                level=LEVEL_INFO, detail="、".join(listening),
            ))
        elif any(getattr(c, "conn_mode", "") == "reverse" for c in connections.values()):
            checks.append(_check(
                "protocol_listen", False, "协议端还没连进来",
                level=LEVEL_WARN, detail="在 NapCat 里把反向 WebSocket 指到本页地址即可", route="/bots", label="查看账号",
            ))

        module_errors = self._module_errors()
        checks.append(_check(
            "modules",
            not module_errors,
            "功能插件正常" if not module_errors else "有插件加载失败",
            level=LEVEL_INFO if not module_errors else LEVEL_WARN,
            detail="、".join(module_errors) if module_errors else "",
            route="/modules" if module_errors else "",
            label="查看插件" if module_errors else "",
        ))

        blockers = [c for c in checks if not c["ok"] and c["level"] == LEVEL_BLOCKER]
        return {
            "ready": not blockers,
            "blockers": len(blockers),
            "checks": checks,
        }

    async def snapshot_async(self) -> dict:
        """带"首启是否完成"的版本（供 API 用）。"""
        data = self.snapshot()
        completed = False
        if self.config_service is not None:
            try:
                completed = bool(await self.config_service.get_kv("setup_completed_at", 0))
            except Exception as e:  # noqa: BLE001 —— 读不到按"未完成"，不影响其它检查
                logger.add_info("Readiness").debug(f"[Readiness] 读首启标记失败: {e}")
                completed = False
        data["setup_completed"] = completed
        return data
