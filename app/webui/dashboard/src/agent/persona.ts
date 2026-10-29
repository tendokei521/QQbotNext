/**
 * 当前人格的统一解析口径。
 *
 * 同一个账号的「当前人格」此前在面板状态条和人格卡里各算一套：
 * 状态条只认 `persona_preset_id`，人格卡还会按 `system_prompt` 内容兜底匹配预设，
 * 于是同一屏能同时出现「人格：未选择」和「当前人格：某某角色设定」。
 *
 * 这里把三种情况收敛成一份结论，所有页面/组件都调用它：
 * 1. 有 `persona_preset_id` → 用该预设名（预设被删则说明已删除）；
 * 2. 没有绑定，但 `system_prompt` 与某个预设正文一致 → 「与「X」内容相同（未绑定）」；
 * 3. 其它 → 未选择。
 */
export interface PersonaPresetLike {
  id: string
  name: string
  system_prompt?: string
}

export interface PersonaResolution {
  /** bound = 有活引用；same-content = 内容与某预设相同但未绑定；none = 未选择 */
  kind: 'bound' | 'same-content' | 'none'
  /** 命中的预设 id（bound/same-content 时有值） */
  presetId?: string
  /** 命中的预设名（bound/same-content 时有值） */
  presetName?: string
  /** 可直接展示的结论文案（不含「人格：」前缀） */
  label: string
}

export function resolvePersona(
  draft: Record<string, any> | null | undefined,
  presets: PersonaPresetLike[],
): PersonaResolution {
  const presetId = String(draft?.persona_preset_id || '').trim()
  const prompt = typeof draft?.system_prompt === 'string' ? draft.system_prompt : ''

  if (presetId) {
    const hit = presets.find((item) => item.id === presetId)
    if (hit) {
      return { kind: 'bound', presetId: hit.id, presetName: hit.name, label: hit.name }
    }
    return { kind: 'bound', presetId, label: '该人格已删除' }
  }

  if (prompt) {
    const same = presets.find((item) => (item.system_prompt || '') === prompt)
    if (same) {
      return {
        kind: 'same-content',
        presetId: same.id,
        presetName: same.name,
        label: `与「${same.name}」内容相同（未绑定）`,
      }
    }
  }

  return { kind: 'none', label: '未选择' }
}
