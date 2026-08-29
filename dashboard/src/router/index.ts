import { createRouter, createWebHashHistory } from 'vue-router'

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
        { path: 'agent/basic', name: 'agent-basic', component: () => import('@/views/AgentBasicPage.vue'), meta: { title: 'Agent 基础配置' } },
        { path: 'agent/model', name: 'agent-model', component: () => import('@/views/AgentModelPage.vue'), meta: { title: 'Agent 模型' } },
        { path: 'agent/behavior', name: 'agent-behavior', component: () => import('@/views/AgentBehaviorPage.vue'), meta: { title: 'Agent 对话行为' } },
        { path: 'agent/stream', name: 'agent-stream', component: () => import('@/views/AgentStreamPage.vue'), meta: { title: 'Agent 流式回复' } },
        { path: 'agent/permission', name: 'agent-permission', component: () => import('@/views/AgentPermissionPage.vue'), meta: { title: 'Agent 权限' } },
        { path: 'agent/memory', name: 'agent-memory', component: () => import('@/views/AgentMemoryPage.vue'), meta: { title: 'Agent 长期记忆' } },
        { path: 'agent/knowledge', name: 'agent-knowledge', component: () => import('@/views/AgentKnowledgePage.vue'), meta: { title: 'Agent 知识库' } },
        { path: 'agent/mcp', name: 'agent-mcp', component: () => import('@/views/AgentMcpPage.vue'), meta: { title: 'Agent MCP 工具' } },
        { path: 'agent/napcat', name: 'agent-napcat', component: () => import('@/views/AgentNapcatPage.vue'), meta: { title: 'Agent NapCat 工具' } },
        { path: 'agent/panels', name: 'agent-panels', component: () => import('@/views/AgentPanelsPage.vue'), meta: { title: 'Agent 定时任务 / 主动消息' } },
        { path: 'provider-presets', name: 'provider-presets', component: () => import('@/views/ProviderPresetsPage.vue'), meta: { title: 'Provider 预设' } },
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
