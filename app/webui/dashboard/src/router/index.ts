import { createRouter, createWebHashHistory } from 'vue-router'
import { AGENT_SECTIONS } from '@/agent/sections'

/**
 * 由 registry 生成子路由：``/agent/*``。
 *
 * 所有配置页共用同一个通用页面 ``AgentSectionPage`` 与同一个外壳 ``AgentLayout``：
 * 分区元数据来自 registry，字段来自后端 schema，定制 UI 来自 customSections，
 * 所以新增分区只需改 sections.json，不必再新增页面文件。
 *
 * 历史：v3 曾并存一个 ``/advanced/*``「进阶调参」区，v4 把其中内容拆成功能模块后
 * 该区只剩一个空概览页，已整体移除（见下方深链表）。
 */
const agentSectionRoutes = AGENT_SECTIONS.map((section) => ({
  path: section.path.replace(/^\/agent\//, ''),
  name: `agent-${section.id}`,
  component: () => import('@/views/AgentSectionPage.vue'),
  meta: { title: section.title },
}))

// 使用 hash 路由：构建产物由 FastAPI 静态挂载，无需服务端 SPA fallback 配置
const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    {
      path: '/',
      component: () => import('@/layouts/DefaultLayout.vue'),
      children: [
        { path: '', name: 'overview', component: () => import('@/views/OverviewPage.vue'), meta: { title: '总览' } },
        { path: 'bots', name: 'bots', component: () => import('@/views/BotsPage.vue'), meta: { title: '账号管理' } },
        { path: 'modules', name: 'modules', component: () => import('@/views/ModulesPage.vue'), meta: { title: '功能模块' } },
        { path: 'modules/agent', redirect: '/agent' },
        { path: 'modules/:name', name: 'module-config', component: () => import('@/views/ModuleConfigPage.vue'), meta: { title: '模块配置' } },
        // Agent 配置区共享外壳：bot 守卫 / 头部 / 保存状态 / 段落导航都在 layout 里
        {
          path: 'agent',
          component: () => import('@/layouts/AgentLayout.vue'),
          children: [
            {
              path: '',
              name: 'agent',
              component: () => import('@/views/AgentPage.vue'),
              meta: { title: 'Agent 面板' },
            },
            ...agentSectionRoutes,
          ],
        },
        // 历史深链：分区重构前的路径统一落到新位置
        { path: 'agent/onebot', redirect: '/tools/onebot' },
        { path: 'agent/role', redirect: '/agent/basic' },
        { path: 'agent/model', redirect: '/agent/basic' },
        // 「进阶中心」已于 v4 拆成模块：工具 / MCP / 知识库 / 主动消息各回自己的模块配置页。
        // 这几条一度指向已删除的 ``/advanced/*``，会落到无匹配路由（白屏），故改指模块页。
        { path: 'agent/tools', redirect: '/modules/llm_tools_extra' },
        { path: 'agent/mcp', redirect: '/modules/llm_tools_extra' },
        { path: 'agent/knowledge', redirect: '/modules/llm_knowledge' },
        { path: 'agent/panels', redirect: '/modules/llm_proactive' },
        // 整个「高级设置」区已移除；旧书签回 Agent 面板，不留无匹配路由
        { path: 'advanced', redirect: '/agent' },
        { path: 'advanced/:section', redirect: '/agent' },
        {
          path: 'tools',
          component: () => import('@/views/ToolsPage.vue'),
          meta: { title: 'Tool 管理' },
          children: [
            { path: 'system', name: 'tools-system', component: () => import('@/views/SystemToolsPage.vue'), meta: { title: 'System Tools' } },
            { path: 'onebot', name: 'tools-onebot', component: () => import('@/views/OnebotToolsPage.vue'), meta: { title: 'OneBot Tools' } },
            { path: 'mcp', name: 'tools-mcp', component: () => import('@/views/McpToolsPage.vue'), meta: { title: 'MCP Tools' } },
            { path: 'module', name: 'tools-module', component: () => import('@/views/ModuleToolsPage.vue'), meta: { title: '模块 Tools' } },
          ],
        },
        { path: 'provider-presets', name: 'provider-presets', component: () => import('@/views/ProviderPresetsPage.vue'), meta: { title: 'Provider 预设' } },
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
