import { defineStore } from 'pinia'
import { ref } from 'vue'
import http, { errorMessage } from '@/api/http'

export interface PromptPresetRow {
  key: string
  label: string
  description: string
  default_text: string
  override_text: string
  effective_text: string
  customized: boolean
}

/**
 * 三套「系统提示词」方案（chat / roleplay / assistant）的本地覆盖。
 *
 * 覆盖是全局的：保存后所有账号都按编辑后的文本组装系统提示词；
 * 账号级 `behavior_rules_override` 仍然优先（那是个别账号的临时覆盖）。
 */
export const usePromptPresetsStore = defineStore('promptPresets', () => {
  const presets = ref<PromptPresetRow[]>([])
  const loading = ref(false)
  const saving = ref('')
  const error = ref('')

  async function load(): Promise<void> {
    loading.value = true
    try {
      const res = await http.get('/api/prompt-presets')
      presets.value = res.data?.presets || []
      error.value = ''
    } catch (err) {
      error.value = errorMessage(err)
    } finally {
      loading.value = false
    }
  }

  async function save(key: string, text: string): Promise<void> {
    saving.value = key
    try {
      const res = await http.put(`/api/prompt-presets/${encodeURIComponent(key)}`, { text })
      presets.value = res.data?.presets || presets.value
    } finally {
      saving.value = ''
    }
  }

  async function reset(key: string): Promise<void> {
    saving.value = key
    try {
      const res = await http.delete(`/api/prompt-presets/${encodeURIComponent(key)}`)
      presets.value = res.data?.presets || presets.value
    } finally {
      saving.value = ''
    }
  }

  return { presets, loading, saving, error, load, save, reset }
})
