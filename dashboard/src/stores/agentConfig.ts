import { defineStore } from 'pinia'
import http, { errorMessage } from '@/api/http'
import { useBotsStore } from '@/stores/bots'
import { useNotifyStore } from '@/stores/notify'
import type { PermissionConfig } from '@/stores/modules'

export type AgentPermission = PermissionConfig

export interface StreamPreset {
  label: string
  description: string
  config: Record<string, any>
}

let autosaveTimer: number | null = null
let pendingBotId: number | null = null
let editSeq = 0
let dirtyFlag = false

export const useAgentConfigStore = defineStore('agentConfig', {
  state: () => ({
    botId: null as number | null,
    loadedFor: null as number | null,
    loading: false,
    enabled: false,
    schema: {} as Record<string, any>,
    config: {} as Record<string, any>,
    draft: {} as Record<string, any>,
    permission: {
      group_mode: 'blacklist',
      group_list: [] as string[],
      user_mode: 'blacklist',
      user_list: [] as string[],
    } as AgentPermission,
    providerModels: [] as { id: string; preset_id: string; preset_name?: string; model: string }[],
    providerPresets: [] as { id: string; name: string }[],
    streamPresets: {} as Record<string, StreamPreset>,
    poolModelIds: [] as string[],
    saveStatus: 'clean' as 'clean' | 'dirty' | 'saving' | 'error',
  }),
  actions: {
    clearDraft() {
      Object.keys(this.draft).forEach((k) => delete this.draft[k])
    },
    async load(force = false) {
      if (autosaveTimer) {
        window.clearTimeout(autosaveTimer)
        autosaveTimer = null
      }
      const bots = useBotsStore()
      if (!bots.bots.length) {
        try {
          await bots.fetchBots()
        } catch {
          /* 列表加载失败时由页面显示空态 */
        }
      }
      if (bots.currentIndex === null && bots.bots.length) {
        bots.restoreSelection()
      }
      const botId = bots.currentBot?.bot_id ?? null
      this.botId = botId
      if (botId === null) return
      if (!force && this.loadedFor === botId && Object.keys(this.draft).length > 0) return

      this.loading = true
      try {
        const res = await http.get<{
          ok: boolean
          bot_id: number | null
          enabled: boolean
          permission: AgentPermission
          config: Record<string, any>
          schema: Record<string, any>
          provider_presets: { id: string; name: string }[]
          provider_models: { id: string; preset_id: string; preset_name?: string; model: string }[]
          stream_presets?: Record<string, StreamPreset>
        }>('/api/agent/config', { params: { bot_id: botId } })
        const data = res.data
        this.enabled = !!data.enabled
        this.schema = data.schema || {}
        this.config = data.config || {}
        this.streamPresets = data.stream_presets || {}
        this.providerPresets = data.provider_presets || []
        this.providerModels = data.provider_models || []
        this.permission = {
          group_mode: data.permission?.group_mode || 'blacklist',
          group_list: [...(data.permission?.group_list || [])],
          user_mode: data.permission?.user_mode || 'blacklist',
          user_list: [...(data.permission?.user_list || [])],
        }
        this.clearDraft()
        Object.assign(this.draft, this.config)
        const storedPool = Array.isArray(this.draft.provider_model_pool)
          ? this.draft.provider_model_pool
          : []
        const legacyPool = [
          this.draft.provider_model_id,
          ...(Array.isArray(this.draft.fallback_model_ids) ? this.draft.fallback_model_ids : []),
        ].filter(Boolean)
        this.poolModelIds = (storedPool.length ? storedPool : legacyPool).map(String)
        this.draft.provider_model_pool = [...this.poolModelIds]
        this.loadedFor = botId
        this.saveStatus = 'clean'
        dirtyFlag = false
      } catch (err) {
        const notify = useNotifyStore()
        notify.push(errorMessage(err), 'error')
      } finally {
        this.loading = false
      }
    },
    scheduleSave() {
      pendingBotId = this.botId
      dirtyFlag = true
      editSeq += 1
      this.saveStatus = 'dirty'
      if (autosaveTimer) window.clearTimeout(autosaveTimer)
      autosaveTimer = window.setTimeout(() => this.save(), 2000)
    },
    onChange(key: string, value: any) {
      this.draft[key] = value
      this.scheduleSave()
    },
    onPermissionChange(v: AgentPermission) {
      this.permission = v
      this.scheduleSave()
    },
    onEnabledChange(v: boolean | null) {
      this.enabled = !!v
      this.scheduleSave()
    },
    async save() {
      if (autosaveTimer) {
        window.clearTimeout(autosaveTimer)
        autosaveTimer = null
      }
      if (!dirtyFlag) return
      const targetBotId = pendingBotId ?? this.botId
      const snapshotSeq = editSeq
      pendingBotId = null
      this.saveStatus = 'saving'
      try {
        await http.post(
          '/api/agent/config',
          { config: { ...this.draft }, permission: this.permission, enabled: this.enabled },
          { params: { bot_id: targetBotId } },
        )
        if (targetBotId !== this.botId) return
        if (editSeq !== snapshotSeq) return
        dirtyFlag = false
        this.saveStatus = 'clean'
        const notify = useNotifyStore()
        notify.push('Agent 配置已保存', 'success')
      } catch (err) {
        if (editSeq !== snapshotSeq) return
        this.saveStatus = 'error'
        const notify = useNotifyStore()
        notify.push(errorMessage(err), 'error')
      }
    },
    syncPool() {
      this.draft.provider_model_pool = [...this.poolModelIds]
      this.onChange('provider_model_pool', [...this.poolModelIds])
    },
    applyConfig(config: Record<string, any>) {
      this.clearDraft()
      Object.assign(this.draft, config || {})
      const storedPool = Array.isArray(this.draft.provider_model_pool) ? this.draft.provider_model_pool : []
      const legacyPool = [
        this.draft.provider_model_id,
        ...(Array.isArray(this.draft.fallback_model_ids) ? this.draft.fallback_model_ids : []),
      ].filter(Boolean)
      this.poolModelIds = (storedPool.length ? storedPool : legacyPool).map(String)
      this.draft.provider_model_pool = [...this.poolModelIds]
      this.scheduleSave()
    },
  },
})
