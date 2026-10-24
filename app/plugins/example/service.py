"""示例模块业务逻辑。"""

from app.core.logger import module_logger, account_label


async def handle(module, event):
    logger = module_logger.add_info(account_label(module.bot_id)).add_info(f"{module.name}")
    logger.info("主函数响应")
