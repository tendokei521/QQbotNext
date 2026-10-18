/**
 * Agent 配置分区 registry。
 *
 * 数据的唯一权威是同目录的 ``sections.json``（后端契约测试也读它），本文件只负责：
 * - 把 JSON 收窄成强类型；
 * - 把「页内子分组」翻译成 schema 过滤结果（见 utils/schema 的 buildSectionSchema）；
 * - 派生路由 / 侧边栏二级菜单 / 概览卡片所需的结构；
 * - 概览卡片的「状态徽标」这类**表现层**逻辑（不适合放进 JSON）。
 *
 * v3 起 registry 自己拥有**页内分组**：一个页面可以横跨多个后端 group，也可以只取某
 * 后端 group 的两三个字段。后端 group 是模块划分（group_session / group_tools …），
 * 用户心智是任务划分，两者不再强行对齐。
 */

import manifest from './sections.json'
import { buildSectionSchema, splitSchema, type VirtualGroupSpec } from '@/utils/schema'

/** 归属区域：agent 走 /agent/*；advanced 走 /advanced/*（进阶调参）。 */
export type AgentSectionArea = 'agent' | 'advanced'

export interface AgentSection {
  id: string
  path: string
  title: string
  subtitle: string
  icon: string
  color: string
  area: AgentSectionArea
  /** 侧边栏/概览内的排序权重 */
  order: number
  /** 页内子分组：决定段内导航、分组标题与字段归属 */
  groups: VirtualGroupSpec[]
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
  area?: AgentSectionArea
  order?: number
  customFirst?: boolean
  groups: VirtualGroupSpec[]
}

const RAW = manifest as unknown as {
  sections: RawSection[]
  retired?: { note?: string; fields?: string[] }
}

export const AGENT_SECTIONS: AgentSection[] = RAW.sections
  .map((s) => ({
    id: s.id,
    path: s.path,
    title: s.title,
    subtitle: s.subtitle,
    icon: s.icon,
    color: s.color,
    area: s.area ?? 'agent',
    order: s.order ?? 100,
    groups: s.groups,
    customFirst: s.customFirst === true,
  }))
  .sort((a, b) => a.order - b.order)

/**
 * 前端不再提供任何入口的字段（后端字段与默认值仍在）。
 *
 * 组装标准化由并行计划接管：信息感知成了固定流程（组装全面化 + 固定上传 tool），
 * 这些「要不要感知/用哪种格式」的开关对用户已经没有意义。
 */
export const RETIRED_FIELDS: string[] = RAW.retired?.fields ?? []

export const AGENT_INDEX_PATH = '/agent'
export const ADVANCED_INDEX_PATH = '/advanced'

export function agentSectionById(id: string): AgentSection | undefined {
  return AGENT_SECTIONS.find((s) => s.id === id)
}

export function agentSectionByPath(path: string): AgentSection | undefined {
  return AGENT_SECTIONS.find((s) => s.path === path)
}

export function sectionsOfArea(area: AgentSectionArea): AgentSection[] {
  return AGENT_SECTIONS.filter((s) => s.area === area)
}

/** 侧边栏「Agent 面板」二级菜单：概览 + 归属 agent 区域的分区。 */
export function agentSidebarEntries(): { to: string; title: string }[] {
  return [
    { to: AGENT_INDEX_PATH, title: '概览' },
    ...sectionsOfArea('agent').map((s) => ({ to: s.path, title: s.title })),
  ]
}

/** 侧边栏「高级设置」二级菜单：概览 + 归属 advanced 区域的分区。 */
export function advancedSidebarEntries(): { to: string; title: string }[] {
  return [
    { to: ADVANCED_INDEX_PATH, title: '概览' },
    ...sectionsOfArea('advanced').map((s) => ({ to: s.path, title: s.title })),
  ]
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
  return buildSectionSchema(schema, section.groups)
}

export interface AgentBadgeInput {
  draft: Record<string, any>
  poolModelIds: string[]
  permission: { group_mode: string; user_mode?: string }
}

/** 概览卡片右上角的状态徽标（纯表现层，故留在 TS 而不是 JSON）。 */
export function agentSectionBadge(id: string, input: AgentBadgeInput): string {
  const draft = input.draft
  switch (id) {
    case 'basic': {
      const on = [draft.group_enable && '群聊', draft.private_enable && '私聊'].filter(Boolean)
      const model = input.poolModelIds.length ? `${input.poolModelIds.length} 个模型` : '未配模型'
      return on.length ? `${on.join(' + ')} · ${model}` : '未开启'
    }
    case 'permission': {
      const owners = (draft.agent_owner_list || []).length
      const managers = (draft.agent_manager_list || []).length
      const roster = owners + managers ? `${owners} 拥有者 / ${managers} 管理者` : '仅档位'
      return roster
    }
    case 'session':
      return draft.history_rounds ? `${draft.history_rounds} 轮历史` : '可调'
    case 'behavior': {
      const debounce = Number(draft.message_debounce_seconds || 0)
      const parts = [draft.interrupt_enable ? '打断开' : '打断关']
      if (debounce > 0) parts.unshift(`抖动 ${debounce}s`)
      return parts.join(' · ')
    }
    case 'stream':
      return draft.stream_output ? '开启' : '关闭'
    case 'memory':
      return draft.memory_enable ? '开启' : '关闭'
    case 'adv_tools': {
      const on = [
        draft.onebot_tools_enable && 'OneBot',
        draft.tavily_enable && '联网',
        draft.knowledge_enable && '知识库',
        draft.mcp_servers && 'MCP',
      ].filter(Boolean)
      return on.length ? on.join(' · ') : '未配置'
    }
    case 'adv_proactive': {
      const on = [
        draft.proactive_friend_enable && '私聊',
        draft.proactive_group_enable && '群聊',
        draft.schedule_enable && '定时',
      ].filter(Boolean)
      return on.length ? on.join(' + ') : '未开启'
    }
    case 'adv_output':
      return draft.speech_via_tool ? '语音气泡' : '纯文本'
    default:
      return ''
  }
}

export type AgentSectionImportance = 'basic' | 'advanced'

/**
 * 分区的重要性：只有当分区内**所有**字段都是 basic 时才算基础分区。
 *
 * 后端为每个字段标注 importance；这里把它变成「哪些页面是新手必需」的口径，
 * 用于概览页的基础/进阶筛选与徽标。
 */
export function agentSectionImportance(
  section: AgentSection,
  schema: Record<string, any>,
): AgentSectionImportance {
  const { items } = splitSchema(agentSectionSchema(section, schema))
  const values = Object.values(items).map((def: any) => def?.importance || 'basic')
  if (!values.length) return 'basic'
  return values.every((v) => v === 'basic') ? 'basic' : 'advanced'
}
