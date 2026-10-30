import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'
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
  /** 生效触发方式：at/keyword/both/all/none */
  trigger_mode: string
  /** 触发方式来自哪一层：default / account / scene / channel */
  trigger_source: string
  trigger_probability: number
  quiet_hours: string
  overridden_keys: string[]
}

/** 触发方式的中文标签（页面、菜单、提示共用一份） */
export const TRIGGER_MODE_LABELS: Record<string, string> = {
  at: '仅 @',
  keyword: '仅关键词',
  both: '@ 或关键词',
  all: '所有消息',
  none: '不触发',
}

export function triggerModeLabel(mode: string): string {
  return TRIGGER_MODE_LABELS[String(mode || 'none')] || '不触发'
}

export interface PolicyDefault {
  enabled: boolean
  enabled_source: string
  /** 账号默认（群聊场景层）的显式值：null = 跟随 Agent 配置 */
  scene_enabled?: boolean | null
  trigger_mode?: string
  trigger_source?: string
  trigger_probability?: number
}

/** 某层（账号层/群层）当前的覆盖行；null 字段在 UI 里表示"继承" */
export interface PolicyOverrideRow {
  enabled?: boolean | null
  config?: Record<string, any>
  permission?: Record<string, any>
  muted_until?: number
}

export interface PolicyDetail {
  resolved: {
    capability: string
    account_id: string
    channel_id: string | null
    enabled: boolean
    enabled_source: string
    config: Record<string, any>
    permission: Record<string, any>
    sources: Record<string, string>
  }
  overrides: { account: PolicyOverrideRow | null; channel: PolicyOverrideRow | null }
  overridable: string[]
  permission_keys: string[]
}

/** 三态：继承账号级默认 / 允许 / 禁止 */
export type SwitchValue = 'inherit' | 'on' | 'off'

/** 判定链的一层：与运行时同一套语义，allowed=false 表示这一层拦下 */
export interface PolicyDecisionCheck {
  stage: string
  label: string
  allowed: boolean
  reason: string
  /** default / account / scene / channel / muted / agent_config / module_authority / module / single_service / responder */
  source: string
  detail?: string
}

/** 同群多账号的响应权仲裁结果 */
export interface PolicyDecisionResponder {
  policy: string
  responders: string[]
  candidates: string[]
  degraded?: boolean | string
}

export interface PolicyDecision {
  account_id: string
  capability: string
  channel_id: string | null
  allowed: boolean
  reason: string
  decided_by: string
  decided_by_label: string
  hint: string
  checks: PolicyDecisionCheck[]
  enabled: boolean
  enabled_source: string
  required_tier: string | null
  required_tier_source: string
  actual_tier: string | null
  config: Record<string, any>
  sources: Record<string, string>
  responder: PolicyDecisionResponder
}

export interface PolicyDecisionResult {
  decision: PolicyDecision
  roles: string[]
  stages: { stage: string; label: string }[]
}

/**
 * 「回复设置」的数据中心：群列表 + 每群开关。
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
  const defaultPolicy = ref<PolicyDefault>({
    enabled: false,
    enabled_source: 'default',
    scene_enabled: null,
  })
  const connected = ref(false)
  const stale = ref(true)
  const fetchedAt = ref(0)
  const error = ref('')
  const search = ref('')
  const savingScene = ref(false)

  // 请求序号：丢弃过期响应，避免快速切换账号时旧账号的群列表覆盖新账号的数据
  let loadSeq = 0

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
    const seq = ++loadSeq
    const aid = accountId.value
    if (!aid) {
      rows.value = []
      loading.value = false
      error.value = ''
      return
    }
    loading.value = true
    error.value = ''
    try {
      const res = await http.get('/api/policy/matrix', { params: { account_id: aid } })
      if (seq !== loadSeq) return
      rows.value = res.data?.rows || []
      defaultPolicy.value = res.data?.default || { enabled: false, enabled_source: 'default' }
    } catch (err) {
      if (seq !== loadSeq) return
      error.value = errorMessage(err)
    } finally {
      if (seq === loadSeq) loading.value = false
    }
  }

  // 切账号：先清空旧账号的数据，再按新账号重拉（各页面无需再各自 watch 账号）
  watch(accountId, () => {
    rows.value = []
    connected.value = false
    stale.value = true
    fetchedAt.value = 0
    error.value = ''
    defaultPolicy.value = { enabled: false, enabled_source: 'default', scene_enabled: null }
    if (accountId.value) void load()
  })

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

  /** 写「账号默认」：null = 跟随 AI 对话面板的开关（清掉场景层覆盖） */
  async function setSceneEnabled(scene: 'group' | 'private', enabled: boolean | null): Promise<void> {
    const aid = accountId.value
    if (!aid) return
    savingScene.value = true
    try {
      await http.post('/api/policy/scene', {
        account_id: aid,
        scene,
        enabled,
      })
      await load()
    } finally {
      savingScene.value = false
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

  /** 读某层（群/账号默认）的完整策略（含继承来源），供高级规则对话框使用 */
  async function getPolicy(channelId: string | null): Promise<PolicyDetail | null> {
    const aid = accountId.value
    if (!aid) return null
    const params: Record<string, string> = { account_id: aid }
    if (channelId) params.channel_id = channelId
    const res = await http.get('/api/policy', { params })
    return res.data as PolicyDetail
  }

  /**
   * 判定链预览：给定群/发信人/身份，问后端「这条消息会不会回、被哪一层拦」。
   * 与运行时同一套语义，前端不自行推断；失败直接抛出，由调用方按现有风格提示。
   */
  async function fetchDecision(options: {
    channelId?: string | null
    userId?: string
    role?: string | null
    capability?: string
  } = {}): Promise<PolicyDecisionResult | null> {
    const aid = accountId.value
    if (!aid) return null
    const params: Record<string, string> = {
      account_id: aid,
      capability: options.capability || 'agent',
    }
    if (options.channelId) params.channel_id = options.channelId
    const userId = String(options.userId || '').trim()
    if (userId) params.user_id = userId
    if (options.role) params.role = options.role
    const res = await http.get('/api/policy/decision', { params })
    const body = (res.data || {}) as {
      decision?: PolicyDecision
      roles?: string[]
      stages?: { stage: string; label: string }[]
    }
    if (!body.decision) return null
    return {
      decision: body.decision,
      roles: body.roles || [],
      stages: body.stages || [],
    }
  }

  /** 写某层覆盖；`clear` 里的键恢复继承 */
  async function savePolicy(
    channelId: string | null,
    patch: Record<string, any>,
    clear: string[] = [],
  ): Promise<void> {
    const aid = accountId.value
    if (!aid) return
    await http.post('/api/policy', {
      account_id: aid,
      channel_id: channelId || undefined,
      patch,
      clear,
    })
    await load()
  }

  return {
    loading,
    refreshing,
    saving,
    savingScene,
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
    setSceneEnabled,
    bulk,
    mute,
    getPolicy,
    fetchDecision,
    savePolicy,
  }
})
