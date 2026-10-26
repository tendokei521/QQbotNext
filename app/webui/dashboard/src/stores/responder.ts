import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import http, { unwrap } from '@/api/http'
import { onSocketMessage } from '@/api/socket'

/** 某群当前的响应权预览（后端 ResponderDecision 序列化） */
export interface ResponderPreview {
  candidates: string[]
  responders: string[]
  reason: string
  degraded: boolean
}

export interface ResponderGroup {
  group_id: string
  name: string
  accounts: string[]
  policy: string
  primary_account_id: string | null
  fallback: 'open' | 'closed'
  preview: ResponderPreview
}

export interface ResponderData {
  module: string
  declared: boolean
  groups: ResponderGroup[]
}

export interface ResponderPolicyInput {
  group_id: string
  policy: string
  primary_account_id: string | null
  fallback: string
}

export interface ResponderSaveResult {
  policies: ResponderPolicyInput[]
  warnings: string[]
  message: string
}

/** 响应权策略中心：按模块加载 / 保存「哪个群由哪个账号响应」 */
export const useResponderStore = defineStore('responder', () => {
  const data = ref<Record<string, ResponderData>>({})
  const loading = ref<Record<string, boolean>>({})
  const saving = ref<Record<string, boolean>>({})

  const dataOf = computed(() => (moduleName: string) => data.value[moduleName] ?? null)

  async function load(moduleName: string): Promise<void> {
    loading.value[moduleName] = true
    try {
      const res = await http.get<ResponderData>(`/api/modules/${encodeURIComponent(moduleName)}/responder`)
      data.value[moduleName] = res.data
    } finally {
      loading.value[moduleName] = false
    }
  }

  async function save(moduleName: string, policies: ResponderPolicyInput[]): Promise<ResponderSaveResult> {
    saving.value[moduleName] = true
    try {
      const res = await http.post(`/api/modules/${encodeURIComponent(moduleName)}/responder`, { policies })
      const body = unwrap<ResponderSaveResult>(res.data)
      await load(moduleName)
      return {
        policies: body?.policies ?? [],
        warnings: body?.warnings ?? [],
        message: body?.message ?? '响应权策略已保存',
      }
    } finally {
      saving.value[moduleName] = false
    }
  }

  // 其他客户端保存后刷新当前模块
  onSocketMessage('responder_updated', (msg) => {
    if (msg && typeof msg.module === 'string') load(msg.module).catch(() => undefined)
  })

  return { data, loading, saving, dataOf, load, save }
})
