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

export function filterSchemaByGroup(
  schema: Record<string, any>,
  groupId: string,
): Record<string, any> {
  const { groups, items } = splitSchema(schema)
  const nextGroups: Record<string, any> = {}
  const nextItems: Record<string, any> = {}
  Object.entries(groups).forEach(([key, def]) => {
    if (key === groupId) nextGroups[key] = def
  })
  Object.entries(items).forEach(([key, def]: [string, any]) => {
    if (def && typeof def === 'object' && def.group === groupId) nextItems[key] = def
  })
  return rebuildSchema(nextGroups, nextItems)
}

export function filterSchemaExcludeGroup(
  schema: Record<string, any>,
  groupId: string,
): Record<string, any> {
  const { groups, items } = splitSchema(schema)
  const nextGroups: Record<string, any> = {}
  const nextItems: Record<string, any> = {}
  Object.entries(groups).forEach(([key, def]) => {
    if (key !== groupId) nextGroups[key] = def
  })
  Object.entries(items).forEach(([key, def]: [string, any]) => {
    if (!(def && typeof def === 'object' && def.group === groupId)) nextItems[key] = def
  })
  return rebuildSchema(nextGroups, nextItems)
}

/** 按页面（page 元数据）过滤 schema：只保留指定页面的字段及其所在分组。 */
export function filterSchemaByPage(
  schema: Record<string, any>,
  page: string,
): Record<string, any> {
  const { groups, items } = splitSchema(schema)
  const nextGroups: Record<string, any> = {}
  const nextItems: Record<string, any> = {}
  const usedGroups = new Set<string>()
  Object.entries(items).forEach(([key, def]: [string, any]) => {
    if (!def || typeof def !== 'object') return
    if (def.page !== page) return
    nextItems[key] = def
    if (def.group) usedGroups.add(String(def.group))
  })
  Object.entries(groups).forEach(([key, def]) => {
    if (usedGroups.has(key)) nextGroups[key] = def
  })
  return rebuildSchema(nextGroups, nextItems)
}

/** 按重要性过滤：basic|advanced|expert，可配合 page 使用。 */
export function filterSchemaByImportance(
  schema: Record<string, any>,
  importance: string,
): Record<string, any> {
  const { groups, items } = splitSchema(schema)
  const nextGroups: Record<string, any> = {}
  const nextItems: Record<string, any> = {}
  const usedGroups = new Set<string>()
  Object.entries(items).forEach(([key, def]: [string, any]) => {
    if (!def || typeof def !== 'object') return
    if (def.importance !== importance) return
    nextItems[key] = def
    if (def.group) usedGroups.add(String(def.group))
  })
  Object.entries(groups).forEach(([key, def]) => {
    if (usedGroups.has(key)) nextGroups[key] = def
  })
  return rebuildSchema(nextGroups, nextItems)
}
