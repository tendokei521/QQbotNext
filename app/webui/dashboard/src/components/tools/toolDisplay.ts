/**
 * 工具展示辅助：把后端给的工具描述收成界面上的中文短名。
 *
 * 规则（与 ToolInfo / 工具总览共用）：
 * - 取 description 的第一个分句（按句号/逗号/顿号/冒号切），再裁剪到 maxLen；
 * - 没有描述时退回工具内部名。
 * 内部名只在副标题/tooltip 里出现，不占主标题。
 */
const CLAUSE_SPLIT = /[。；;，,、：:]/

export function toolDisplayTitle(
  tool: { name: string; description?: string },
  maxLen = 18,
): string {
  const desc = (tool.description || '').replace(/\s+/g, ' ').trim()
  if (!desc) return tool.name
  const first = desc.split(CLAUSE_SPLIT)[0].trim()
  const base = first || tool.name
  return base.length > maxLen ? `${base.slice(0, maxLen)}…` : base
}
