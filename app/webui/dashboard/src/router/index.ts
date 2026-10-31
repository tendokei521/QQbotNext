import { createRouter, createWebHashHistory } from 'vue-router'
import { AGENT_SECTIONS } from '@/agent/sections'

/**
 * 由 registry 生成子路由：``/ai/settings/*``。
 *
 * 3 个深度设置页共用同一个通用页面 ``AgentSectionPage`` 与同一个外壳 ``AgentLayout``：
 * 分区元数据来自 registry，字段来自后端 schema，所以新增分区只需改 sections.json，
 * 不必再新增页面文件。
 *
 * 历史：``/agent/*`` 与 ``/advanced/*`` 已随信息架构调整下线，旧链接在下方深链表里
 * 统一重定向到新位置（AI 对话面板 / 回复与权限 / AI 设置）。
 */
const agentSectionRoutes = AGENT_SECTIONS.map((section) => ({
  path: section.path.replace(/^\/ai\/settings\//, ''),
  name: `agent-${section.id}`,
  component: () => import('@/views/AgentSectionPage.vue'),
  meta: { title: section.title },
}))

// 使用 hash 路由：构建产物由 FastAPI 静态挂载，无需服务端 SPA fallback 配置
const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    // 首启向导：全屏（不套侧栏），完成后回总览
    {
      path: '/setup',
      name: 'setup',
      component: () => import('@/views/SetupPage.vue'),
      meta: { title: '首次配置' },
    },
    {
      path: '/',
      component: () => import('@/layouts/DefaultLayout.vue'),
      children: [
        { path: '', name: 'overview', component: () => import('@/views/OverviewPage.vue'), meta: { title: '总览' } },
        { path: 'bots', name: 'bots', component: () => import('@/views/BotsPage.vue'), meta: { title: '账号管理' } },
        // AI 对话面板：日常操作台（替代原「AI 对话设置 → 概览」）
        { path: 'ai', name: 'ai-panel', component: () => import('@/views/AiPanelPage.vue'), meta: { title: 'AI 对话面板' } },
        // 回复与权限：原「回复设置」+ 原「权限管理」合并成一页三个 Tab
        { path: 'reply', name: 'reply', component: () => import('@/views/PolicyPage.vue'), meta: { title: '回复与权限' } },
        { path: 'modules', name: 'modules', component: () => import('@/views/ModulesPage.vue'), meta: { title: '功能插件' } },
        // 插件响应策略：同群多账号「谁响应」，独立面板 + 单插件独立配置页
        {
          path: 'responder',
          name: 'responder-overview',
          component: () => import('@/views/ResponderOverviewPage.vue'),
          meta: { title: '插件响应策略' },
        },
        {
          path: 'responder/:name',
          name: 'responder-module',
          component: () => import('@/views/ResponderModulePage.vue'),
          meta: { title: '插件响应策略' },
        },
        {
          path: 'bundles',
          name: 'bundles',
          component: () => import('@/views/BundlesPage.vue'),
          meta: { title: '配置包' },
        },
        { path: 'modules/agent', redirect: '/ai' },
        { path: 'modules/:name', name: 'module-config', component: () => import('@/views/ModuleConfigPage.vue'), meta: { title: '功能设置' } },
        // AI 设置：共用外壳（bot 守卫 / 头部 / 保存状态 / 段落导航都在 layout 里）
        {
          path: 'ai/settings',
          component: () => import('@/layouts/AgentLayout.vue'),
          children: [
            { path: '', redirect: '/ai/settings/behavior' },
            ...agentSectionRoutes,
            // 拆分前的子路径（basic / permission / 其他拼错的段）在 v6 已不存在，
            // 没有兜底就整页白屏。统一回 AI 对话面板，旧书签不至于「打不开」。
            { path: ':rest(.*)', redirect: '/ai' },
          ],
        },
        // 历史深链：AI 对话设置拆分前的路径统一落到新位置，不留白屏
        { path: 'agent', redirect: '/ai' },
        { path: 'agent/basic', redirect: '/ai' },
        { path: 'agent/permission', redirect: { path: '/reply', query: { tab: 'who' } } },
        { path: 'agent/behavior', redirect: '/ai/settings/behavior' },
        { path: 'agent/session', redirect: '/ai/settings/session' },
        { path: 'agent/advanced', redirect: '/ai/settings/advanced' },
        { path: 'agent/onebot', redirect: '/tools/onebot' },
        { path: 'agent/role', redirect: '/ai' },
        { path: 'agent/model', redirect: '/ai' },
        // 「进阶中心」已拆成模块：工具 / MCP / 知识库 / 主动消息各回自己的模块配置页
        { path: 'agent/tools', redirect: '/modules/llm_tools_extra' },
        { path: 'agent/mcp', redirect: '/modules/llm_tools_extra' },
        { path: 'agent/knowledge', redirect: '/modules/llm_knowledge' },
        { path: 'agent/panels', redirect: '/modules/llm_proactive' },
        // 整个「高级设置」区已移除；旧书签回 AI 对话面板，不留无匹配路由
        { path: 'advanced', redirect: '/ai' },
        { path: 'advanced/:section', redirect: '/ai' },
        // 原「回复设置」改名「回复与权限」
        { path: 'policy', redirect: '/reply' },
        {
          path: 'tools',
          component: () => import('@/views/ToolsPage.vue'),
          meta: { title: '高级工具' },
          children: [
            {
              path: 'overview',
              name: 'tools-overview',
              component: () => import('@/components/tools/ToolsOverview.vue'),
              meta: { title: '工具总览' },
            },
            { path: 'system', name: 'tools-system', component: () => import('@/views/SystemToolsPage.vue'), meta: { title: '系统工具' } },
            { path: 'onebot', name: 'tools-onebot', component: () => import('@/views/OnebotToolsPage.vue'), meta: { title: 'OneBot 工具' } },
            { path: 'mcp', name: 'tools-mcp', component: () => import('@/views/McpToolsPage.vue'), meta: { title: '外部工具' } },
            { path: 'module', name: 'tools-module', component: () => import('@/views/ModuleToolsPage.vue'), meta: { title: '插件工具' } },
          ],
        },
        { path: 'provider-presets', name: 'provider-presets', component: () => import('@/views/ProviderPresetsPage.vue'), meta: { title: '模型服务' } },
        { path: 'personas', name: 'personas', component: () => import('@/views/PersonasPage.vue'), meta: { title: '人格设定' } },
        { path: 'sessions', name: 'sessions', component: () => import('@/views/SessionsPage.vue'), meta: { title: '会话数据' } },
        // 「配置档案」(/config-profiles) 已移除：它是实验性功能，且整套能力（前端页 +
        // 后端 API + 服务 + DB 表 + 运行时按 UMO 覆盖）随之下线。实验性开关框架仍在。
        { path: 'config-profiles', redirect: '/settings' },
        { path: 'stats', name: 'stats', component: () => import('@/views/StatsPage.vue'), meta: { title: '数据统计' } },
        { path: 'logs', name: 'logs', component: () => import('@/views/LogsPage.vue'), meta: { title: '日志' } },
        { path: 'settings', name: 'settings', component: () => import('@/views/SettingsPage.vue'), meta: { title: '设置' } },
      ],
    },
  ],
})

router.afterEach((to) => {
  document.title = to.meta.title ? `${to.meta.title} · QQBot Next` : 'QQBot Next 管理后台'
})

export default router
