import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'
import http, { unwrap } from '@/api/http'
import { onSocketMessage } from '@/api/socket'
import { useBotsStore } from './bots'

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

/**
 * 响应权策略中心：按模块加载 / 保存「哪个群由哪个账号响应」。
 *
 * 缓存键是「账号 + 模块」：同一份策略虽然按模块存，但「当前响应者预览」取决于此刻有哪些账号在线，
 * 切账号后旧预览会误导用户（显示别的账号在响应）。以前只按模块名缓存，切号读到的是旧账号的预览。
 */
export const useResponderStore = defineStore('responder', () => {
  const bots = useBotsStore()
  const data = ref<Record<string, ResponderData>>({})
  const loading = ref<Record<string, boolean>>({})
  const saving = ref<Record<string, boolean>>({})

  function cacheKey(moduleName: string): string {
    return `${bots.currentAccountId ?? '-'}::${moduleName}`
  }

  const dataOf = computed(
    () => (moduleName: string) => data.value[cacheKey(moduleName)] ?? null,
  )
  const loadingOf = computed(() => (moduleName: string) => !!loading.value[cacheKey(moduleName)])
  const savingOf = computed(() => (moduleName: string) => !!saving.value[cacheKey(moduleName)])

  async function load(moduleName: string): Promise<void> {
    const key = cacheKey(moduleName)
    loading.value[key] = true
    try {
      const res = await http.get<ResponderData>(`/api/modules/${encodeURIComponent(moduleName)}/responder`)
      // 加载期间切了账号：丢弃旧账号的响应，避免写进新账号的缓存槽
      if (key !== cacheKey(moduleName)) return
      data.value[key] = res.data
    } finally {
      if (key in loading.value) loading.value[key] = false
    }
  }

  async function save(moduleName: string, policies: ResponderPolicyInput[]): Promise<ResponderSaveResult> {
    const key = cacheKey(moduleName)
    saving.value[key] = true
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
      if (key in saving.value) saving.value[key] = false
    }
  }

  // 切账号：旧账号的预览/加载态全部作废，由页面按需重新 load
  watch(
    () => bots.currentAccountId,
    () => {
      data.value = {}
      loading.value = {}
      saving.value = {}
    },
  )

  // 其他客户端保存后刷新当前模块
  onSocketMessage('responder_updated', (msg) => {
    if (msg && typeof msg.module === 'string') load(msg.module).catch(() => undefined)
  })

  return { data, loading, saving, dataOf, loadingOf, savingOf, load, save }
})
