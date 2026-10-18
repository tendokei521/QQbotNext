/**
 * Agent 配置分区 registry。
 *
 * 数据的唯一权威是同目录的 ``sections.json``（后端契约测试也读它），本文件只负责：
 * - 把 JSON 收窄成强类型；
 * - 派生路由 / 侧边栏二级菜单 / 概览卡片所需的结构；
 * - 概览卡片的「状态徽标」这类**表现层**逻辑（不适合放进 JSON）。
 *
 * 之前同一份「11 个分区」被抄在 5 处（router、DefaultLayout、AgentPage、10 个
 * AgentSubPage 的 props、后端 _PAGE_BY_GROUP），改一个分区要动 5 个文件 2 种语言。
 * 现在一律从这里派生。
 */

import manifest from './sections.json'
import { filterSchemaBySection, splitSchema } from '@/utils/schema'

export type AgentSectionArea = 'agent' | 'tools'

export interface AgentSection {
  /** 分区 id，同时是后端 schema 的 page 元数据取值，保持深链兼容 */
  id: string
  path: string
  title: string
  subtitle: string
  icon: string
  color: string
  /** 该分区渲染哪些 schema group（顺序即展示顺序）；与 fields 二选一 */
  groups: string[]
  /** 需要从某个 group 里只取部分字段时的显式清单（覆盖 groups） */
  fields?: string[]
  /** 定制组件是否排在 schema 表单之前（默认之后） */
  customFirst: boolean
  /** 归属区域：agent 走 /agent/*；tools 复用 Tool 管理下的既有页面 */
  area: AgentSectionArea
}

interface RawSection {
  id: string
  path: string
  title: string
  subtitle: string
  icon: string
  color: string
  groups: string[]
  fields?: string[]
  customFirst?: boolean
  area?: AgentSectionArea
}

const RAW = manifest as unknown as { sections: RawSection[] }

export const AGENT_SECTIONS: AgentSection[] = RAW.sections.map((s) => ({
  id: s.id,
  path: s.path,
  title: s.title,
  subtitle: s.subtitle,
  icon: s.icon,
  color: s.color,
  groups: s.groups,
  fields: s.fields,
  customFirst: s.customFirst === true,
  area: s.area ?? 'agent',
}))

export const AGENT_INDEX_PATH = '/agent'

export function agentSectionById(id: string): AgentSection | undefined {
  return AGENT_SECTIONS.find((s) => s.id === id)
}

export function agentSectionByPath(path: string): AgentSection | undefined {
  return AGENT_SECTIONS.find((s) => s.path === path)
}

/** 侧边栏「Agent 面板」二级菜单：概览 + 归属 agent 区域的分区（onebot 归 Tool 管理）。 */
export function agentSidebarEntries(): { to: string; title: string }[] {
  return [
    { to: AGENT_INDEX_PATH, title: '概览' },
    ...AGENT_SECTIONS.filter((s) => s.area === 'agent').map((s) => ({
      to: s.path,
      title: s.title,
    })),
  ]
}

/** 字段所属分区：显式 fields 命中优先，其次按 group 归属。 */
export function agentSectionForField(key: string, group?: string): AgentSection | undefined {
  const byField = AGENT_SECTIONS.find((s) => s.fields?.includes(key))
  if (byField) return byField
  if (!group) return undefined
  return AGENT_SECTIONS.find((s) => s.groups.includes(group))
}

/**
 * 被某个分区用 ``fields`` 显式独占的字段。
 *
 * 这些字段不再被「凭所属分组」选中的其它分区顺带渲染——否则 group_session 里的
 * system_prompt / persona_preset_id 既会出现在「基础配置」也会出现在「角色设定」。
 */
const CARVED_OUT_FIELDS: string[] = Array.from(
  new Set(AGENT_SECTIONS.flatMap((s) => s.fields || [])),
)

/** 把分区转成 utils/schema 的过滤选择器。 */
export function agentSectionSelector(section: AgentSection): {
  groups: string[]
  fields?: string[]
  excludeFields?: string[]
} {
  if (section.fields && section.fields.length) {
    return { groups: [], fields: section.fields }
  }
  return { groups: section.groups, excludeFields: CARVED_OUT_FIELDS }
}

export interface AgentBadgeInput {
  draft: Record<string, any>
  poolModelIds: string[]
  permission: { group_mode: string }
}

/** 概览卡片右上角的状态徽标（纯表现层，故留在 TS 而不是 JSON）。 */
export function agentSectionBadge(id: string, input: AgentBadgeInput): string {
  const draft = input.draft
  switch (id) {
    case 'basic': {
      const on = [draft.group_enable && '群聊', draft.private_enable && '私聊'].filter(Boolean)
      return on.length ? on.join(' + ') : '未开启'
    }
    case 'role':
      return draft.system_prompt ? '已设置' : '未设置'
    case 'model':
      return input.poolModelIds.length ? `${input.poolModelIds.length} 个模型` : '未配置模型'
    case 'session':
      return draft.history_rounds ? `${draft.history_rounds} 轮历史` : '可调'
    case 'behavior':
      return draft.interrupt_enable ? '打断已开' : '打断关闭'
    case 'stream':
      return draft.stream_output ? '开启' : '关闭'
    case 'memory':
      return draft.memory_enable ? '开启' : '关闭'
    case 'knowledge':
      return draft.knowledge_enable ? '开启' : '关闭'
    case 'tools':
      if (draft.mcp_servers) return 'MCP 已配'
      return draft.tavily_enable ? 'Tavily 开' : '未配置'
    case 'panels':
      return '管理'
    case 'permission':
      return input.permission.group_mode === 'blacklist' ? '黑名单' : '白名单'
    case 'onebot':
      return draft.onebot_tools_enable ? '开启' : '关闭'
    default:
      return ''
  }
}

export type AgentSectionImportance = 'basic' | 'advanced'

/**
 * 分区的重要性：只有当分区内**所有**字段都是 basic 时才算基础分区。
 *
 * 后端早就为每个字段标注了 importance，但前端此前从未使用；这里把它变成
 * 「哪些分区是新手必需」的口径，用于概览页的基础/高级筛选与徽标。
 */
export function agentSectionImportance(
  section: AgentSection,
  schema: Record<string, any>,
): AgentSectionImportance {
  const { items } = splitSchema(filterSchemaBySection(schema, agentSectionSelector(section)))
  const values = Object.values(items).map((def: any) => def?.importance || 'basic')
  if (!values.length) return 'basic'
  return values.every((v) => v === 'basic') ? 'basic' : 'advanced'
}

/**
 * 分组图标：仅供段内导航/分组标题使用，纯展示，未收录的分组回退小圆点。
 * 这份映射不参与「哪个字段属于哪页」的判定，故不进 sections.json。
 */
const GROUP_ICONS: Record<string, string> = {
  group_switch: 'mdi-toggle-switch-outline',
  group_trigger: 'mdi-call-split',
  group_session: 'mdi-chat-processing-outline',
  group_context: 'mdi-account-details-outline',
  group_interrupt: 'mdi-hand-back-right-outline',
  group_stream: 'mdi-wave',
  group_model: 'mdi-api',
  group_permission: 'mdi-shield-account-outline',
  group_memory: 'mdi-brain',
  group_knowledge: 'mdi-book-open-variant',
  group_tools: 'mdi-toolbox-outline',
  group_tavily: 'mdi-web',
  group_mcp: 'mdi-server-network',
  group_onebot: 'mdi-robot-industrial',
  group_proactive: 'mdi-bell-ring-outline',
  group_schedule: 'mdi-clock-outline',
  group_compress: 'mdi-arrow-collapse-vertical',
}

export function agentGroupIcon(groupId: string): string {
  return GROUP_ICONS[groupId] || 'mdi-circle-small'
}
