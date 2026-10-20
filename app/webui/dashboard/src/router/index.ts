import { createRouter, createWebHashHistory } from 'vue-router'
import { AGENT_SECTIONS } from '@/agent/sections'

/**
 * 由 registry 生成子路由：``/agent/*``（常用）与 ``/advanced/*``（进阶调参）。
 *
 * 两个区域共用同一个通用页面 ``AgentSectionPage`` 与同一个外壳 ``AgentLayout``：
 * 分区元数据来自 registry，字段来自后端 schema，定制 UI 来自 customSections，
 * 所以新增分区只需改 sections.json，不必再新增页面文件。
 */
function sectionRoutes(area: 'agent' | 'advanced', prefix: string) {
  return AGENT_SECTIONS.filter((section) => section.area === area).map((section) => ({
    path: section.path.replace(new RegExp(`^${prefix}/`), ''),
    name: `${area}-${section.id}`,
    component: () => import('@/views/AgentSectionPage.vue'),
    meta: { title: section.title },
  }))
}

const agentSectionRoutes = sectionRoutes('agent', '/agent')
const advancedSectionRoutes = sectionRoutes('advanced', '/advanced')

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
        // 进阶调参区：与 Agent 面板同级，共用同一个外壳（bot 守卫 / 保存状态）
        {
          path: 'advanced',
          component: () => import('@/layouts/AgentLayout.vue'),
          children: [
            {
              path: '',
              name: 'advanced',
              component: () => import('@/views/AdvancedPage.vue'),
              meta: { title: '高级设置' },
            },
            ...advancedSectionRoutes,
          ],
        },
        // 历史深链：分区重构前的路径统一落到新位置
        { path: 'agent/onebot', redirect: '/tools/onebot' },
        { path: 'agent/role', redirect: '/agent/basic' },
        { path: 'agent/model', redirect: '/agent/basic' },
        { path: 'agent/tools', redirect: '/advanced/tools' },
        { path: 'agent/mcp', redirect: '/advanced/tools' },
        { path: 'agent/knowledge', redirect: '/advanced/tools' },
        { path: 'agent/panels', redirect: '/advanced/proactive' },
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
