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
