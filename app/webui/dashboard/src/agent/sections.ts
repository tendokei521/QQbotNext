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
 *
 * v4 起只有**一个区域**（``/agent``）。曾并存的 ``/advanced``「进阶调参」区在内容
 * 拆成功能模块后只剩空概览页，连同 ``area`` 字段一并移除——单值的枚举没有信息量，
 * 留着只会让人以为还有第二个区域。
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

export const AGENT_INDEX_PATH = '/agent'

export function agentSectionById(id: string): AgentSection | undefined {
  return AGENT_SECTIONS.find((s) => s.id === id)
}

export function agentSectionByPath(path: string): AgentSection | undefined {
  return AGENT_SECTIONS.find((s) => s.path === path)
}

/** 侧边栏「Agent 面板」二级菜单：概览 + 全部分区。 */
export function agentSidebarEntries(): { to: string; title: string }[] {
  return [
    { to: AGENT_INDEX_PATH, title: '概览' },
    ...AGENT_SECTIONS.map((s) => ({ to: s.path, title: s.title })),
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
  return buildSectionSchema(schema, section.groups, section.customFields)
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
