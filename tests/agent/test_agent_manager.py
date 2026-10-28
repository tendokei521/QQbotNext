"""AgentManager / AgentRuntime 装配测试（框架级）。"""

from app.agent.runtime import AgentManager, AgentRuntime


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


async def test_agent_manager_ensure_and_get():
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
        await mgr.shutdown()


async def test_agent_runtime_config_and_components():
    cfg_svc = _CfgSvc()
    rt = AgentRuntime(5, cfg_svc, _TM())
    try:
        # 默认值生效
        assert rt.config.get("schedule_enable") is True
        assert rt.config.get("api_key", "") == ""
        assert rt.config.get("system_prompt", "") == "你是一个友好的助手。"
        # 组件已装配
        # 主动消息 / 定时任务是业务：由 `llm_proactive` / `llm_schedule` 模块在 on_load
        # 时装进运行时；未装模块时为 None（流水线侧的调用点都有 getattr 保护）。
        assert rt.scheduler is None
        assert rt.proactive is None
        assert rt.session_mgr is not None
        # 策略配置视图默认未接管（= 回退 agent 配置）
        assert rt.assembly_options is None
        assert rt.tools_options is None
        # 配置写入与读取
        rt.config.set("api_key", "sk-test")
        assert rt.config.get("api_key") == "sk-test"
        assert cfg_svc.data.get(("agent", "5"))["api_key"] == "sk-test"
        # 模块兼容接口
        assert rt.bot_id == 5
        assert rt.ctx.bot is None  # 未注入 bot
        assert rt.ctx.services.task_manager is not None
    finally:
        await rt.stop()


async def test_runtime_creation_is_silent_but_toggling_is_logged(caplog):
    """启动装配是默认行为，不打日志；「开/关」是用户动作，必须留一行。"""
    import logging

    mgr = AgentManager(_CfgSvc(), _TM())
    try:
        with caplog.at_level(logging.INFO, logger="app.agent.runtim"):
            runtime = mgr.ensure_runtime(1)
        assert runtime is not None
        assert "运行时已装配" not in caplog.text

        caplog.clear()
        with caplog.at_level(logging.INFO, logger="app.agent.config"):
            runtime.config.set_enabled(False)
        assert "已停用" in caplog.text

        caplog.clear()
        with caplog.at_level(logging.INFO, logger="app.agent.config"):
            runtime.config.set_enabled(False)      # 同上，不算变化
        assert "已停用" not in caplog.text

        caplog.clear()
        with caplog.at_level(logging.INFO, logger="app.agent.config"):
            runtime.config.set_enabled(True)
        assert "已启用" in caplog.text
    finally:
        await mgr.shutdown()
