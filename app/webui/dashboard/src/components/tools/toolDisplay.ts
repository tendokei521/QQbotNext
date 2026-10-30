/**
 * 工具展示辅助：把后端给的工具描述收成界面上的中文短名与「剩余说明」。
 *
 * 规则（ToolRow / 工具总览共用）：
 * - 标题取 description 的第一个分句（按句号/逗号/顿号/冒号切），再裁剪到 maxLen；
 * - 标题已经用掉了那一段，正文里要把这段去掉再显示，否则同一个意思会说两遍；
 * - 没有描述时退回工具内部名（内部名只在副标题/tooltip 出现，不占主标题）。
 */
const CLAUSE_SPLIT = /[。；;，,、：:]/

function normalize(description?: string): string {
  return (description || '').replace(/\s+/g, ' ').trim()
}

export function toolDisplayTitle(
  tool: { name: string; description?: string },
  maxLen = 18,
): string {
  const desc = normalize(tool.description)
  if (!desc) return tool.name
  const first = desc.split(CLAUSE_SPLIT)[0].trim()
  const base = first || tool.name
  return base.length > maxLen ? `${base.slice(0, maxLen)}…` : base
}

/**
 * 标题之外剩下的说明：去掉标题那段、再去掉紧挨着的分隔符。
 *
 * 例：description「获取当前会话的基础信息：会话类型（群聊/私聊）、当前时间。」
 *     → title「获取当前会话的基础信息」，rest「会话类型（群聊/私聊）、当前时间。」
 */
export function toolDisplayRest(tool: { name: string; description?: string }): string {
  const desc = normalize(tool.description)
  if (!desc) return ''
  const title = toolDisplayTitle(tool)
  const base = title.endsWith('…') ? title.slice(0, -1) : title
  if (!desc.startsWith(base)) return desc
  return desc.slice(base.length).replace(/^[。；;，,、：:\s]+/, '').trim()
}

/** 列表里的一行简描述（≤40 字，超出截断）；没有剩余说明时退回完整描述 */
export function toolDisplayBrief(
  tool: { name: string; description?: string },
  maxLen = 40,
): string {
  const desc = normalize(tool.description)
  if (!desc) return ''
  const rest = toolDisplayRest(tool) || desc
  return rest.length > maxLen ? `${rest.slice(0, maxLen)}…` : rest
}
