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

/** 自动保存去抖时长（毫秒） */
const AUTOSAVE_DEBOUNCE_MS = 2000

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

    // ↓ 自动保存的记账状态。此前是模块级可变变量（autosaveTimer / pendingBotId /
    //   editSeq / dirtyFlag），等同于全局单例：不进 devtools、无法随 store 重置，
    //   并且在 store 重建（HMR / 测试）后仍残留上一轮的值。
    autosaveTimer: null as number | null,
    pendingBotId: null as number | null,
    editSeq: 0,
    dirty: false,
  }),
  actions: {
    clearDraft() {
      Object.keys(this.draft).forEach((k) => delete this.draft[k])
    },

    /** 取消未触发的自动保存计时器（不影响已有改动是否保存） */
    cancelAutosave() {
      if (this.autosaveTimer !== null) {
        window.clearTimeout(this.autosaveTimer)
        this.autosaveTimer = null
      }
    },

    /** 重置为初始状态：切到「无 Bot」或登出时避免残留上一个 Bot 的配置。 */
    reset() {
      this.cancelAutosave()
      this.$reset()
    },

    /**
     * 把未保存的改动立即落盘。
     *
     * 用于 Bot 切换 / 强制重载之前——此前 load() 直接 clearTimeout 丢弃计时器，
     * 未保存的改动会静默消失。
     */
    async flush() {
      if (!this.dirty) return
      if ((this.pendingBotId ?? this.botId) === null) return
      await this.save()
    },

    async load(force = false) {
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

      // 先把旧 Bot 的改动落盘，再切换/重载，避免静默丢失编辑
      await this.flush()

      this.botId = botId
      if (botId === null) {
        // 没有可用 Bot：清空上一个 Bot 的配置，避免界面继续显示过期草稿
        this.reset()
        return
      }
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
        this.poolModelIds = resolvePoolIds(this.draft)
        this.draft.provider_model_pool = [...this.poolModelIds]
        this.loadedFor = botId
        this.saveStatus = 'clean'
        this.dirty = false
        this.pendingBotId = null
        this.editSeq = 0
      } catch (err) {
        const notify = useNotifyStore()
        notify.push(errorMessage(err), 'error')
      } finally {
        this.loading = false
      }
    },

    scheduleSave() {
      this.pendingBotId = this.botId
      this.dirty = true
      this.editSeq += 1
      this.saveStatus = 'dirty'
      this.cancelAutosave()
      this.autosaveTimer = window.setTimeout(() => {
        this.autosaveTimer = null
        void this.save()
      }, AUTOSAVE_DEBOUNCE_MS)
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
      this.cancelAutosave()
      if (!this.dirty) return
      const targetBotId = this.pendingBotId ?? this.botId
      if (targetBotId === null) return
      const snapshotSeq = this.editSeq
      this.pendingBotId = null
      this.saveStatus = 'saving'
      try {
        await http.post(
          '/api/agent/config',
          { config: { ...this.draft }, permission: this.permission, enabled: this.enabled },
          { params: { bot_id: targetBotId } },
        )
        // 保存期间用户又改了：保持 dirty，等下一轮
        if (this.editSeq !== snapshotSeq) return
        this.dirty = false
        this.saveStatus = 'clean'
        const notify = useNotifyStore()
        notify.push('Agent 配置已保存', 'success')
      } catch (err) {
        if (this.editSeq !== snapshotSeq) return
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
      this.poolModelIds = resolvePoolIds(this.draft)
      this.draft.provider_model_pool = [...this.poolModelIds]
      this.scheduleSave()
    },
  },
})

/**
 * 模型池解析：优先 provider_model_pool，其次回退旧的单模型 + fallback 列表。
 * load 与 applyConfig 共用，避免两处各写一份解析逻辑。
 */
function resolvePoolIds(draft: Record<string, any>): string[] {
  const stored = Array.isArray(draft.provider_model_pool) ? draft.provider_model_pool : []
  const legacy = [
    draft.provider_model_id,
    ...(Array.isArray(draft.fallback_model_ids) ? draft.fallback_model_ids : []),
  ].filter(Boolean)
  return (stored.length ? stored : legacy).map(String)
}
