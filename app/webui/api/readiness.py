"""就绪诊断与首启状态 API。

- ``GET  /api/readiness``：一份聚合状态（启动横幅 / 总览就绪卡 / 向导完成判定共用）；
- ``POST /api/setup/complete``：标记首启向导已完成（之后不再自动进入）。

刻意不在这里做"修复动作"：修账号/模型/开关都有各自的页面与接口，
这里只负责给出"差什么、去哪修"，避免又长出一套并行的写入口。
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.application.readiness_service import ReadinessService
from app.webui.api.deps import (
    get_agent_manager,
    get_bot_service,
    get_config_service,
    get_module_registry,
)

router = APIRouter(tags=["readiness"])


def _service(bot_service, config_service, agent_manager, module_registry) -> ReadinessService:
    return ReadinessService(
        config_service=config_service,
        gateway=getattr(bot_service, "gateway", None),
        agent_manager=agent_manager,
        module_registry=module_registry,
    )


@router.get("/readiness")
async def api_readiness(
    bot_service=Depends(get_bot_service),
    config_service=Depends(get_config_service),
    agent_manager=Depends(get_agent_manager),
    module_registry=Depends(get_module_registry),
):
    data = await _service(bot_service, config_service, agent_manager, module_registry).snapshot_async()
    return JSONResponse(content={"ok": True, **data})


@router.post("/setup/complete")
async def api_setup_complete(request: Request, config_service=Depends(get_config_service)):
    await config_service.set_kv("setup_completed_at", int(time.time()))
    # 向导结束就别再后台每 2s 重试了（用户之后可在账号管理里手动连/重连）
    try:
        container = getattr(request.app.state, "container", None)
        if container is not None:
            from app.application.setup_account import SetupAccountService

            container.get(SetupAccountService).stop_connecting()
    except Exception as e:  # noqa: BLE001 —— 收尾失败不该让"完成"接口报错
        import logging

        logging.getLogger(__name__).debug(f"[Setup] 完成后停止连接重试失败: {e}")
    return JSONResponse(content={"ok": True, "message": "首次配置已完成"})
