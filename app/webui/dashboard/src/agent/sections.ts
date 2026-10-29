/**
 * AI 设置分区 registry。
 *
 * 数据的唯一权威是同目录的 ``sections.json``（后端契约测试也读它），本文件只负责：
 * - 把 JSON 收窄成强类型；
 * - 把「页内子分组」翻译成 schema 过滤结果（见 utils/schema 的 buildSectionSchema）；
 * - 派生路由 / 侧边栏二级菜单所需的结构；
 * - 分区状态徽标这类**表现层**逻辑（不适合放进 JSON）。
 *
 * v3 起 registry 自己拥有**页内分组**：一个页面可以横跨多个后端 group，也可以只取某
 * 后端 group 的两三个字段。后端 group 是模块划分（group_session / group_tools …），
 * 用户心智是任务划分，两者不再强行对齐。
 *
 * v5 起「AI 对话设置」拆分为「AI 对话面板（/ai）+ AI 设置（本文件 3 页，/ai/settings/*）」，
 * 概览页与基础配置/权限管理页已删除，二级菜单只剩 3 个深度设置页。
 */

import manifest from './sections.json'
import { buildSectionSchema, type VirtualGroupSpec } from '@/utils/schema'

export interface AgentSection {
  id: string
  path: string
  title: string
  subtitle: string
  icon: string
  color: string
  /** 侧边栏/概览内的排序权重 */
  order: number
  /** 页内子分组：决定段内导航、分组标题与字段归属 */
  groups: VirtualGroupSpec[]
  /**
   * 由本页定制卡片渲染、不在 schema 表单里重复出现的字段。
   *
   * 例如「基础配置」的人格预设选择器已经做成卡片，`persona_preset_id` 不该再在
   * 表单里出现一次；但它的**归属**仍由 groups 声明，契约测试照样能校验覆盖率。
   */
  customFields: string[]
  /** 定制组件是否排在 schema 表单之前（默认之后） */
  customFirst: boolean
}

interface RawSection {
  id: string
  path: string
  title: string
  subtitle: string
  icon: string
  color: string
  order?: number
  customFirst?: boolean
  customFields?: string[]
  groups: VirtualGroupSpec[]
}

const RAW = manifest as unknown as { sections: RawSection[] }

export const AGENT_SECTIONS: AgentSection[] = RAW.sections
  .map((s) => ({
    id: s.id,
    path: s.path,
    title: s.title,
    subtitle: s.subtitle,
    icon: s.icon,
    color: s.color,
    order: s.order ?? 100,
    groups: s.groups,
    customFields: s.customFields ?? [],
    customFirst: s.customFirst === true,
  }))
  .sort((a, b) => a.order - b.order)

export const AGENT_INDEX_PATH = '/ai'

export function agentSectionById(id: string): AgentSection | undefined {
  return AGENT_SECTIONS.find((s) => s.id === id)
}

export function agentSectionByPath(path: string): AgentSection | undefined {
  return AGENT_SECTIONS.find((s) => s.path === path)
}

/** 侧边栏「AI 设置」二级菜单：只剩 3 个深度设置页（概览已删除）。 */
export function agentSidebarEntries(): { to: string; title: string }[] {
  return AGENT_SECTIONS.map((s) => ({ to: s.path, title: s.title }))
}

/** 字段归属哪个页面的哪个子分组（配置搜索跳转用）。 */
export function agentSectionForField(
  key: string,
  group?: string,
): { section: AgentSection; groupId: string } | undefined {
  for (const section of AGENT_SECTIONS) {
    for (const spec of section.groups) {
      if (spec.fields?.includes(key)) return { section, groupId: spec.id }
      if (group && (spec.source || []).includes(group) && !(spec.excludeFields || []).includes(key)) {
        return { section, groupId: spec.id }
      }
    }
  }
  return undefined
}

/** 把分区转成 ConfigForm 能直接渲染的 schema（已按本页子分组重排）。 */
export function agentSectionSchema(
  section: AgentSection,
  schema: Record<string, any>,
): Record<string, any> {
  return buildSectionSchema(schema, section.groups, section.customFields)
}

export interface AgentBadgeInput {
  draft: Record<string, any>
  poolModelIds: string[]
  permission: { group_mode: string; user_mode?: string }
}

/** 分区状态徽标（纯表现层，故留在 TS 而不是 JSON）。 */
export function agentSectionBadge(id: string, input: AgentBadgeInput): string {
  const draft = input.draft
  switch (id) {
    case 'session':
      return draft.history_rounds ? `${draft.history_rounds} 轮历史` : '可调'
    case 'behavior': {
      const debounce = Number(draft.message_debounce_seconds || 0)
      const parts = [draft.interrupt_enable ? '打断开' : '打断关']
      if (debounce > 0) parts.unshift(`抖动 ${debounce}s`)
      return parts.join(' · ')
    }
    case 'advanced': {
      const on = [
        draft.image_understanding_enable && '图片',
        draft.gif_understanding_enable && '动图',
        draft.tavily_enable && '联网',
      ].filter(Boolean)
      return on.length ? on.join(' · ') : '未开启'
    }
    default:
      return ''
  }
}
