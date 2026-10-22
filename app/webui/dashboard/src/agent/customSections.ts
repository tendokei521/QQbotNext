/**
 * 需要定制 UI 的分区：分区 id → 组件。
 *
 * 未列出的分区只渲染 schema 配置表单（走 ``AgentConfigSection``），
 * 所以新增纯配置分区**不必动这个文件**。
 *
 * 约定：这里挂的组件必须自包含——不接收 props，直接从 store 读取所需数据，
 * 这样才能被 ``AgentSectionPage`` 用统一的 ``<component :is>`` 挂载。
 *
 * 一个页面需要多块定制 UI 时，用一个**组合组件**包起来（如 AgentBasicSection
 * = 总开关 + 模型池 + 人格预设），而不是让 registry 支持多组件——否则
 * 「定制 UI 排在哪」就会在 registry 里长出一套排序语法。
 */
import { defineAsyncComponent, type Component } from 'vue'

const CUSTOM_SECTION_LOADERS: Record<string, () => Promise<unknown>> = {
  basic: () => import('@/components/agent/sections/AgentBasicSection.vue'),
  permission: () => import('@/components/agent/sections/AgentPermissionSection.vue'),
}

// defineAsyncComponent 会创建新的组件包装，缓存避免每次渲染重建（导致重复请求）
const resolved = new Map<string, Component>()

export function agentCustomSection(id: string): Component | null {
  const loader = CUSTOM_SECTION_LOADERS[id]
  if (!loader) return null
  let component = resolved.get(id)
  if (!component) {
    component = defineAsyncComponent(loader as () => Promise<Component>)
    resolved.set(id, component)
  }
  return component
}
