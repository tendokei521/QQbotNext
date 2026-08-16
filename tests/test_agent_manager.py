"""AgentManager / AgentRuntime 装配测试（框架级）。"""

from app.llm.manager import AgentManager, AgentRuntime


class _CfgSvc:
    def __init__(self):
        self.data = {}
        self.auth = {}

    def get_module_config(self, module, bot_id):
        return dict(self.data.get((module, str(bot_id)), {}) or {})

    def set_module_config(self, module, bot_id, config, persist=True):
        self.data[(module, str(bot_id))] = dict(config)

    async def save_module_config(self, module, bot_id, config):
        self.data[(module, str(bot_id))] = dict(config)

    def get_module_authority(self, module, bot_id):
        return dict(self.auth.get((module, str(bot_id)), {}) or {})

    def set_module_authority(self, module, bot_id, authority):
        self.auth[(module, str(bot_id))] = dict(authority)


class _TM:
    def create_task(self, coro, **kw):
        return None  # 测试不真正运行后台任务


def test_agent_manager_ensure_and_get():
    mgr = AgentManager(_CfgSvc(), _TM())
    try:
        assert mgr.get_runtime(1) is None
        rt = mgr.ensure_runtime(1)
        assert rt is not None
        assert rt.bot_id == 1
        assert mgr.get_runtime(1) is rt
        # 全局 None 不创建运行时
        assert mgr.ensure_runtime(None) is None
    finally:
        mgr.shutdown()


def test_agent_runtime_config_and_components():
    cfg_svc = _CfgSvc()
    rt = AgentRuntime(5, cfg_svc, _TM())
    try:
        # 默认值生效
        assert rt.config.get("schedule_enable") is True
        assert rt.config.get("api_key", "") == ""
        assert rt.config.get("system_prompt", "") == "你是一个友好的助手。"
        # 组件已装配
        assert rt.scheduler is not None
        assert rt.proactive is not None
        assert rt.session_mgr is not None
        # 配置写入与读取
        rt.config.set("api_key", "sk-test")
        assert rt.config.get("api_key") == "sk-test"
        assert cfg_svc.data.get(("agent", "5"))["api_key"] == "sk-test"
        # 模块兼容接口
        assert rt.bot_id == 5
        assert rt.ctx.bot is None  # 未注入 bot
        assert rt.ctx.services.task_manager is not None
    finally:
        rt.stop()
