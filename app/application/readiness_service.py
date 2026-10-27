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
) -> dict:
    return {
        "id": check_id,
        "ok": bool(ok),
        "level": level,
        "title": title,
        "detail": detail,
        "action": {"route": route, "label": label} if route else None,
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
        except Exception:  # noqa: BLE001
            return []

    def _connections(self) -> dict:
        try:
            return dict(getattr(self.gateway, "connections", {}) or {})
        except Exception:  # noqa: BLE001
            return {}

    def _runtimes(self) -> dict:
        try:
            return dict(self.agent_manager.runtimes() or {})
        except Exception:  # noqa: BLE001
            return {}

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
        except Exception:  # noqa: BLE001
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

        connections = self._connections()
        connected = [
            aid for aid, conn in connections.items()
            if getattr(conn, "status", "") == "connected"
        ]
        checks.append(_check(
            "connected",
            bool(connected),
            "账号已连接" if connected else "账号未连接",
            detail="、".join(str(x) for x in connected) if connected else "连接成功后才能收发消息",
            route="/bots",
            label="去连接",
        ))

        runtimes = self._runtimes()
        pools = {
            str(aid): self._pool_of(self._config_of(runtime))
            for aid, runtime in runtimes.items()
        }
        any_pool = any(pools.values())
        checks.append(_check(
            "model_pool",
            any_pool,
            "Agent 已选择模型" if any_pool else "Agent 未选择模型",
            detail="" if any_pool else "在 Agent → 基础配置里选择模型并加入模型池",
            route="/agent/basic",
            label="选择模型",
        ))

        scenes = {
            str(aid): bool(cfg.get("group_enable") or cfg.get("private_enable"))
            for aid, cfg in ((str(aid), self._config_of(rt)) for aid, rt in runtimes.items())
        }
        any_scene = any(scenes.values())
        checks.append(_check(
            "scene_enabled",
            any_scene,
            "已开启响应场景" if any_scene else "群聊与私聊都未开启",
            detail="" if any_scene else "群聊回复关闭时，群里 @ 机器人不会有任何回应",
            route="/agent/basic",
            label="开启群聊回复",
        ))

        # 协议端监听/连接：反向账号看 listening，正向账号已被 connected 覆盖
        listening: list[str] = []
        for aid, conn in connections.items():
            try:
                if getattr(conn, "conn_mode", "") == "reverse" and self.gateway.reverse_link.listening(aid):
                    listening.append(str(aid))
            except Exception:  # noqa: BLE001
                continue
        if listening:
            checks.append(_check(
                "protocol_listen", True, "反向 WebSocket 正在监听",
                level=LEVEL_INFO, detail="、".join(listening),
            ))
        elif any(getattr(c, "conn_mode", "") == "reverse" for c in connections.values()):
            checks.append(_check(
                "protocol_listen", False, "反向 WebSocket 未监听",
                level=LEVEL_WARN, detail="协议端（如 NapCat）还没连进来", route="/bots", label="查看账号",
            ))

        module_errors = self._module_errors()
        checks.append(_check(
            "modules",
            not module_errors,
            "功能模块加载正常" if not module_errors else "有模块加载失败",
            level=LEVEL_INFO if not module_errors else LEVEL_WARN,
            detail="、".join(module_errors) if module_errors else "",
            route="/modules" if module_errors else "",
            label="查看模块" if module_errors else "",
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
            except Exception:  # noqa: BLE001
                completed = False
        data["setup_completed"] = completed
        return data
