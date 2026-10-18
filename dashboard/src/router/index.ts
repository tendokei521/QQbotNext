import { createRouter, createWebHashHistory } from 'vue-router'
import { AGENT_SECTIONS } from '@/agent/sections'

// 分区 id → 页面组件。动态 import 无法由数据派生，只能显式映射；
// 其余导航元数据（路径/标题/图标）一律来自 @/agent/sections。
const AGENT_PAGE_COMPONENTS: Record<string, () => Promise<unknown>> = {
  basic: () => import('@/views/AgentBasicPage.vue'),
  role: () => import('@/views/AgentRolePage.vue'),
  model: () => import('@/views/AgentModelPage.vue'),
  behavior: () => import('@/views/AgentBehaviorPage.vue'),
  stream: () => import('@/views/AgentStreamPage.vue'),
  permission: () => import('@/views/AgentPermissionPage.vue'),
  memory: () => import('@/views/AgentMemoryPage.vue'),
  knowledge: () => import('@/views/AgentKnowledgePage.vue'),
  mcp: () => import('@/views/AgentMcpPage.vue'),
  panels: () => import('@/views/AgentPanelsPage.vue'),
}

/**
 * 由 registry 生成 /agent/* 子路由。
 *
 * 只生成 ``area === 'agent'`` 的分区：``onebot`` 分区的界面挂在 Tool 管理下
 * （``/tools/onebot``），这里不再重复注册，仅保留 ``/agent/onebot`` 的历史重定向。
 *
 * 缺组件时立刻抛错而不是让 vue-router 在跳转时抛一句看不懂的话——
 * 这是「往 sections.json 加了分区却忘了加页面」的快速失败点。
 */
const agentSectionRoutes = AGENT_SECTIONS.filter((section) => section.area === 'agent').map(
  (section) => {
    const component = AGENT_PAGE_COMPONENTS[section.id]
    if (!component) {
      throw new Error(
        `[agent-sections] 分区 "${section.id}" 缺少页面组件映射，请补进 router/index.ts 的 AGENT_PAGE_COMPONENTS`,
      )
    }
    return {
      path: section.path.replace(/^\//, ''),
      name: `agent-${section.id}`,
      component,
      meta: { title: `Agent ${section.title}` },
    }
  },
)

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
        { path: 'agent', name: 'agent', component: () => import('@/views/AgentPage.vue'), meta: { title: 'Agent 面板' } },
        // OneBot 工具界面归属 Tool 管理，保留历史路径重定向
        { path: 'agent/onebot', redirect: '/tools/onebot' },
        ...agentSectionRoutes,
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
        { path: 'config-profiles', name: 'config-profiles', component: () => import('@/views/ConfigProfilesPage.vue'), meta: { title: '配置档案' } },
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
