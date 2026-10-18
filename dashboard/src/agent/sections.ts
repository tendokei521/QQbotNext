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

export interface AgentBadgeInput {
  draft: Record<string, any>
  poolModelIds: string[]
  permission: { group_mode: string }
}

/** 概览卡片右上角的状态徽标（纯表现层，故留在 TS 而不是 JSON）。 */
export function agentSectionBadge(id: string, input: AgentBadgeInput): string {
  const draft = input.draft
  switch (id) {
    case 'basic':
      return '已配置'
    case 'role':
      return draft.system_prompt ? '已设置' : '未设置'
    case 'model':
      return input.poolModelIds.length ? `${input.poolModelIds.length} 个模型` : '未配置模型'
    case 'behavior':
      return '可调'
    case 'stream':
      return draft.stream_output ? '开启' : '关闭'
    case 'permission':
      return input.permission.group_mode === 'blacklist' ? '黑名单' : '白名单'
    case 'memory':
      return draft.memory_enable ? '开启' : '关闭'
    case 'knowledge':
      return draft.knowledge_enable ? '开启' : '关闭'
    case 'mcp':
      return draft.mcp_servers ? '已配置' : '未配置'
    case 'onebot':
      return draft.onebot_tools_enable ? '开启' : '关闭'
    case 'panels':
      return '管理'
    default:
      return ''
  }
}
