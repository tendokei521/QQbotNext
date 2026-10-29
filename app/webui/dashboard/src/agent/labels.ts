/**
 * 权限档位的统一中文叫法。
 *
 * 插件列表、插件配置页、回复与权限、OneBot 工具权限覆盖共用一份，避免
 * 「所有人 / 仅主人 / 群成员」「不限制 / 所有者 / 成员」这类同义不同词。
 * 键与后端 `permission` 的取值一致。
 */
export const PERMISSION_LABELS: Record<string, string> = {
  everyone: '所有人',
  member: '群成员及以上',
  group_admin: '群管及以上',
  group_owner: '仅群主',
  manager: '管理者与拥有者',
  owner: '仅拥有者',
}

/** 权限档位 chip 的悬浮说明 */
export const PERMISSION_TOOLTIP = '谁能触发这个插件'

export function permissionLabel(value: string | null | undefined): string {
  if (!value) return '未设置'
  return PERMISSION_LABELS[value] || value
}
