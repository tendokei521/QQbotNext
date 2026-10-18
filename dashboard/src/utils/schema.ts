/** Agent 配置 schema 分组过滤工具（兼容扁平旧式与 {groups, items} 新式）。 */

function isGroupDef(def: any): boolean {
  return !!def && typeof def === 'object' && def.type === 'group'
}

export function splitSchema(schema: Record<string, any>): { groups: Record<string, any>; items: Record<string, any> } {
  const raw = schema || {}
  if (raw.items && typeof raw.items === 'object' && (raw.groups || typeof raw.groups === 'object')) {
    return {
      groups: raw.groups || {},
      items: raw.items || {},
    }
  }
  const groups: Record<string, any> = {}
  const items: Record<string, any> = {}
  Object.entries(raw).forEach(([key, def]: [string, any]) => {
    if (isGroupDef(def)) groups[key] = def
    else items[key] = def
  })
  return { groups, items }
}

function rebuildSchema(groups: Record<string, any>, items: Record<string, any>): Record<string, any> {
  // 后端当前返回新式结构；为兼容旧式扁平，若输入没有显式新式标记则返回扁平。
  // 这里统一返回新式结构，ConfigForm 两种都能解析。
  return { groups, items }
}

/** 分区选择器：由 @/agent/sections 的 registry 提供。 */
export interface SchemaSectionSelector {
  /** 该分区渲染哪些分组 */
  groups?: string[]
  /** 需要「从分组里只取几项」时的显式字段清单，优先于 groups */
  fields?: string[]
  /**
   * 被**其它**分区用 fields 独占的字段：即使其所属分组在本分区的 groups 里，
   * 也不在本分区渲染。避免同一个配置项在两个分区各出现一次。
   */
  excludeFields?: string[]
}

/**
 * 按分区选择器过滤 schema。
 *
 * - ``fields`` 非空时先按字段名收窄（其所在分组一并保留），用于「某分组只有几个字段
 *   属于本分区」的情况（例如 group_session 里只有 system_prompt / persona_preset_id
 *   属于角色设定页）；
 * - 否则按 ``groups`` 成员过滤，并剔除被其它分区独占的字段（excludeFields）；
 * - 分组顺序始终沿用 schema 原顺序，避免导航顺序随配置漂移。
 */
export function filterSchemaBySection(
  schema: Record<string, any>,
  selector: SchemaSectionSelector,
): Record<string, any> {
  const { groups, items } = splitSchema(schema)
  const wantedGroups = new Set(selector.groups || [])
  const wantedFields = selector.fields ? new Set(selector.fields) : null
  const excludedFields = new Set(selector.excludeFields || [])
  const nextGroups: Record<string, any> = {}
  const nextItems: Record<string, any> = {}
  const usedGroups = new Set<string>()

  Object.entries(items).forEach(([key, def]: [string, any]) => {
    if (!def || typeof def !== 'object') return
    const included = wantedFields
      ? wantedFields.has(key)
      : !!def.group && wantedGroups.has(String(def.group)) && !excludedFields.has(key)
    if (!included) return
    nextItems[key] = def
    if (def.group) usedGroups.add(String(def.group))
  })
  Object.entries(groups).forEach(([key, def]) => {
    if (usedGroups.has(key)) nextGroups[key] = def
  })
  return rebuildSchema(nextGroups, nextItems)
}

/**
 * 分组锚点 DOM id：与 ConfigForm 内部 ``:id="`group-${g.id}`"`` 保持一致，
 * 段内导航（PageSectionNav）据此 scrollIntoView。
 */
export function configGroupDomId(groupId: string): string {
  return `group-${groupId}`
}

/** 页内子分组：由 registry 声明，决定本页的段内导航与卡片分组标题。 */
export interface VirtualGroupSpec {
  id: string
  label: string
  icon?: string
  /** 显式字段清单（顺序即展示顺序），优先于 source */
  fields?: string[]
  /** 收编整个后端 group（其后端分组标题在本页被改写成本子分组的标题） */
  source?: string[]
  excludeFields?: string[]
}

/**
 * 把整份 schema 重组成「本页自己的子分组」。
 *
 * 为什么需要这一层：后端 group 是按**模块**划分的（group_session / group_proactive /
 * group_tools …），而用户的心智模型是按**任务**划分的（会话管理 / 主动消息 / 工具能力）。
 * 一个任务常常横跨多个后端 group（会话管理就要取自 group_session + group_compress +
 * group_tools），而后端 group 的标题对用户没有意义（「工具调用」里混着图片和动图规则）。
 *
 * 所以这里直接改写 ``item.group`` 成页内子分组 id，让 ConfigForm 原样按本页结构渲染：
 * 分组标题、顺序、段内导航全部来自 registry，后端不需要知道前端怎么排版。
 */
export function buildSectionSchema(
  schema: Record<string, any>,
  specs: VirtualGroupSpec[],
): { groups: Record<string, any>; items: Record<string, any> } {
  const { items } = splitSchema(schema)
  const groups: Record<string, any> = {}
  const nextItems: Record<string, any> = {}

  specs.forEach((spec) => {
    const excluded = new Set(spec.excludeFields || [])
    const keys =
      spec.fields && spec.fields.length
        ? spec.fields
        : Object.entries(items)
            .filter(([key, def]: [string, any]) => {
              const group = def?.group ? String(def.group) : ''
              return !!group && (spec.source || []).includes(group) && !excluded.has(key)
            })
            .map(([key]) => key)

    const present = keys.filter((key) => items[key])
    if (!present.length) return

    groups[spec.id] = {
      type: 'group',
      label: spec.label,
      collapsible: true,
      ...(spec.icon ? { icon: spec.icon } : {}),
    }
    present.forEach((key) => {
      nextItems[key] = { ...items[key], group: spec.id }
    })
  })

  return { groups, items: nextItems }
}
