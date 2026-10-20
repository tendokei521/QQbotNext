"""群申请管理模块声明。

按群独立配置入群申请自动审核：
- 关键词过滤（满足/不满足时忽略或拒绝）
- QQ 等级过滤
- 黑名单用户
"""

from app.modules import BaseModule, module_hook

from .config_schema import SCHEMA


class Module(BaseModule):
    name = "群申请管理"
    sign = "GroupRequest"
    description = "按群独立配置入群申请自动审核（关键词 / QQ等级 / 黑名单）"
    permission = "owner"
    category = "请求"
    tags = ["入群", "审核"]
    order = 10
    default_config = {
        "group_request_configs": {},
        "group_request_configs_selected": "",
    }
    config_schema = SCHEMA
    DYNAMIC_PROVIDERS = {
        "group_request_configs": "dynamic_group_request_configs",
    }

    @module_hook("request_group", order=10)
    async def handle_group_request(self, event):
        from .service import handle_group_request

        await handle_group_request(self, event)

    async def dynamic_group_request_configs(self, field, bot, value=None):
        """动态数据源：群列表选项 / 某群的独立配置字段。"""
        from .service import dynamic_fields, dynamic_options

        if value is None:
            return await dynamic_options(self, bot)
        return await dynamic_fields(self, bot, value)
