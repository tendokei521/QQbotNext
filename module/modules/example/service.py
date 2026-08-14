"""示例模块业务逻辑。"""

from app.core.logger import module_logger


async def handle(module, event):
    logger = module_logger.add_info(f"#{module.bot_id}").add_info(f"{module.name}")
    logger.info("主函数响应")
