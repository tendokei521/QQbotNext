import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import http, { errorMessage } from '@/api/http'
import { useBotsStore } from './bots'

/** 群列表 × 生效状态（后端 /api/policy/matrix） */
export interface PolicyMatrixRow {
  group_id: string
  name: string
  member_count: number
  in_group: boolean
  enabled: boolean
  /** default / account / channel / muted */
  enabled_source: string
  muted_until: number
  overridden_keys: string[]
}

export interface PolicyDefault {
  enabled: boolean
  enabled_source: string
}

/** 三态：继承账号级默认 / 允许 / 禁止 */
export type SwitchValue = 'inherit' | 'on' | 'off'

/**
 * 「权限与回复」的数据中心：群列表 + 每群开关。
 *
 * 只做简单视图（哪个群能聊天）；高级规则（触发/回复/频控）后续在同一份数据上扩展。
 * 读接口一次拉全矩阵，写接口按群/批量，写完重新拉——避免前端维护第二套"继承"逻辑。
 */
export const usePolicyStore = defineStore('policy', () => {
  const bots = useBotsStore()
  const loading = ref(false)
  const refreshing = ref(false)
  const saving = ref<string | null>(null)
  const rows = ref<PolicyMatrixRow[]>([])
  const defaultPolicy = ref<PolicyDefault>({ enabled: false, enabled_source: 'default' })
  const connected = ref(false)
  const stale = ref(true)
  const fetchedAt = ref(0)
  const error = ref('')
  const search = ref('')

  const accountId = computed(() => bots.currentAccountId)
  const filtered = computed(() => {
    const kw = search.value.trim().toLowerCase()
    if (!kw) return rows.value
    return rows.value.filter(
      (row) => row.name.toLowerCase().includes(kw) || row.group_id.includes(kw),
    )
  })
  const allowedCount = computed(() => rows.value.filter((row) => row.enabled).length)
  const mutedCount = computed(
    () => rows.value.filter((row) => row.muted_until > Date.now() / 1000).length,
  )

  function switchValueOf(row: PolicyMatrixRow): SwitchValue {
    if (row.enabled_source === 'channel') return row.enabled ? 'on' : 'off'
    return 'inherit'
  }

  async function load(): Promise<void> {
    const aid = accountId.value
    if (!aid) {
      rows.value = []
      return
    }
    loading.value = true
    error.value = ''
    try {
      const res = await http.get('/api/policy/matrix', { params: { account_id: aid } })
      rows.value = res.data?.rows || []
      defaultPolicy.value = res.data?.default || { enabled: false, enabled_source: 'default' }
    } catch (err) {
      error.value = errorMessage(err)
    } finally {
      loading.value = false
    }
  }

  /** 拉取群列表：账号未连接时后端返回 409 + 可读原因 */
  async function refreshGroups(): Promise<number> {
    const aid = accountId.value
    if (!aid) return 0
    refreshing.value = true
    error.value = ''
    try {
      const res = await http.post('/api/groups/refresh', null, { params: { account_id: aid } })
      connected.value = true
      fetchedAt.value = Number(res.data?.fetched_at || 0)
      stale.value = false
      await load()
      return Number(res.data?.count || 0)
    } catch (err) {
      error.value = errorMessage(err)
      throw err
    } finally {
      refreshing.value = false
    }
  }

  async function setSwitch(row: PolicyMatrixRow, value: SwitchValue): Promise<void> {
    const aid = accountId.value
    if (!aid) return
    saving.value = row.group_id
    try {
      const enabled = value === 'inherit' ? null : value === 'on'
      await http.post('/api/policy', {
        account_id: aid,
        channel_id: row.group_id,
        patch: { enabled },
      })
      await load()
    } finally {
      saving.value = null
    }
  }

  async function bulk(enabled: boolean | null): Promise<number> {
    const aid = accountId.value
    if (!aid || !rows.value.length) return 0
    saving.value = '__bulk__'
    try {
      const res = await http.post('/api/policy/bulk', {
        account_id: aid,
        channel_ids: rows.value.map((row) => row.group_id),
        enabled,
      })
      await load()
      return Number(res.data?.changed || 0)
    } finally {
      saving.value = null
    }
  }

  async function mute(row: PolicyMatrixRow, minutes: number): Promise<void> {
    const aid = accountId.value
    if (!aid) return
    saving.value = row.group_id
    try {
      await http.post('/api/policy/mute', {
        account_id: aid,
        channel_id: row.group_id,
        minutes,
      })
      await load()
    } finally {
      saving.value = null
    }
  }

  return {
    loading,
    refreshing,
    saving,
    rows,
    defaultPolicy,
    connected,
    stale,
    fetchedAt,
    error,
    search,
    accountId,
    filtered,
    allowedCount,
    mutedCount,
    switchValueOf,
    load,
    refreshGroups,
    setSwitch,
    bulk,
    mute,
  }
})
