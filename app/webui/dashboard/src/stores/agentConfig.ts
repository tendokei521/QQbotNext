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

/** 模块拥有的配置键 → 归属模块名。与后端 `MODULE_KEYS` 对应，
 * 仅用于把保存请求分流到对应模块的配置接口。 */
const MODULE_OWNED_FIELDS: Record<string, string> = {
  onebot_tools_enable: 'llm_tools_extra',
  onebot_tools_allowed: 'llm_tools_extra',
  onebot_tools_denied: 'llm_tools_extra',
  onebot_tool_overrides: 'llm_tools_extra',
  onebot_tools_max_result: 'llm_tools_extra',
  onebot_tools_debug: 'llm_tools_extra',
  tool_result_directive_enable: 'llm_tools_extra',
  tool_result_directive: 'llm_tools_extra',
}

export const useAgentConfigStore = defineStore('agentConfig', {
  state: () => ({
    botId: null as string | null,
    loadedFor: null as string | null,
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
    saveStatus: 'clean' as 'clean' | 'dirty' | 'saving' | 'error' | 'blocked',
    /** 配置加载失败原因（空串 = 正常）。离线/账号缺失时页面靠它显示提示，而不是弹红条 */
    loadError: '',

    // ↓ 自动保存的记账状态。此前是模块级可变变量（autosaveTimer / pendingBotId /
    //   editSeq / dirtyFlag），等同于全局单例：不进 devtools、无法随 store 重置，
    //   并且在 store 重建（HMR / 测试）后仍残留上一轮的值。
    autosaveTimer: null as number | null,
    pendingBotId: null as string | null,
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
      if (bots.currentId === null && bots.bots.length) {
        bots.restoreSelection()
      }
      const botId = bots.currentAccountId

      // 先把旧 Bot 的改动落盘，再切换/重载，避免静默丢失编辑
      await this.flush()

      this.botId = botId
      if (botId === null) {
        // 没有任何账号：清空上一个 Bot 的配置，避免界面继续显示过期草稿。
        // 注意这不是「未连接」——账号存在但没连上时照常可编辑（配置不依赖连接）。
        this.reset()
        return
      }
      if (!force && this.loadedFor === botId && Object.keys(this.draft).length > 0) {
        // 配置整份不重拉（会覆盖未保存的草稿），但 Provider 目录是跨页共享的只读数据，
        // 可能刚在「Provider 预设」页加过模型 —— 不刷新的话模型池里要等整页刷新才看得到。
        await this.refreshProviderCatalog()
        return
      }

      this.loading = true
      try {
        const res = await http.get<{
          ok: boolean
          account_id: string | null
          enabled: boolean
          permission: AgentPermission
          config: Record<string, any>
          schema: Record<string, any>
          provider_presets: { id: string; name: string }[]
          provider_models: { id: string; preset_id: string; preset_name?: string; model: string }[]
          stream_presets?: Record<string, StreamPreset>
        }>('/api/agent/config', { params: { account_id: botId } })
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
        this.loadError = ''
      } catch (err) {
        // 加载失败**不弹红条**：未连接/账号刚建好都可能出现瞬时失败，页面用 loadError 提示即可
        this.loadError = errorMessage(err)
      } finally {
        this.loading = false
      }
    },

    /**
     * 只刷新 Provider 目录（连接预设 + 模型实例），不动配置草稿。
     *
     * 「添加模型」之类的入口在独立页面：改完回到 Agent 页时 `load()` 会命中
     * 「同账号 + 已有草稿」缓存直接返回，于是模型池下拉一直拿着旧目录，
     * 新模型要整页刷新（store 重建）才出现。目录与草稿互不相干，单独重拉即可。
     */
    async refreshProviderCatalog() {
      try {
        const [presetsRes, modelsRes] = await Promise.all([
          http.get<{ ok: boolean; presets: { id: string; name: string }[] }>('/api/provider-presets'),
          http.get<{ ok: boolean; models: { id: string; preset_id: string; model: string }[] }>(
            '/api/provider-models',
          ),
        ])
        const presets = presetsRes.data?.presets || []
        const nameById = new Map(presets.map((p) => [p.id, p.name]))
        this.providerPresets = presets.map((p) => ({ id: p.id, name: p.name }))
        // 规则与后端 /api/agent/config 一致：模型列表本身不带 preset_name，这里按预设表补齐，
        // 否则模型池会退化成只显示裸 id。
        this.providerModels = (modelsRes.data?.models || []).map((m) => ({
          id: m.id,
          preset_id: m.preset_id,
          preset_name: nameById.get(m.preset_id) || m.preset_id,
          model: m.model,
        }))
      } catch {
        // 目录刷新失败保留上一次结果：页面照常可用，加载错误由 load() 的 loadError 负责呈现
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

    /**
     * 没有选定账号时：只保留页面上的草稿并标「blocked」，不排自动保存。
     *
     * 账号是配置的落库归属（v4：账号一创建就有 id，与是否连接无关），一个账号都没有时
     * 改动无处可存——页面上必须如实说"不保存"，而不是静默丢掉或假装已保存。
     */
    markBlockedIfNoAccount(): boolean {
      if (this.botId !== null) return false
      this.saveStatus = 'blocked'
      return true
    },

    onChange(key: string, value: any) {
      this.draft[key] = value
      if (this.markBlockedIfNoAccount()) return
      this.scheduleSave()
    },

    onPermissionChange(v: AgentPermission) {
      this.permission = v
      if (this.markBlockedIfNoAccount()) return
      this.scheduleSave()
    },

    onEnabledChange(v: boolean | null) {
      this.enabled = !!v
      if (this.markBlockedIfNoAccount()) return
      this.scheduleSave()
    },

    /**
     * 保存草稿；返回是否真的落盘成功。
     *
     * 失败原因仍由本 action 直接提示（红条），返回值只给「覆盖式操作」的调用方判断
     * 能不能说「已应用」——以前这里内部吞错，调用方 await 完就无条件提示成功。
     */
    async save(): Promise<boolean> {
      this.cancelAutosave()
      if (!this.dirty) return true
      const targetBotId = this.pendingBotId ?? this.botId
      if (targetBotId === null) {
        // 没有账号就没有落库归属：不要静默，也不要让调用方误以为保存成功
        useNotifyStore().push('未选择账号，配置没有保存', 'warning')
        return false
      }
      const snapshotSeq = this.editSeq
      this.pendingBotId = null
      this.saveStatus = 'saving'
      try {
        // 拆分：模块拥有的键走模块配置，其余走 agent 配置（写读必须同一个抽屉）
        const moduleBuckets: Record<string, Record<string, unknown>> = {}
        const agentDraft: Record<string, unknown> = {}
        for (const [key, value] of Object.entries(this.draft)) {
          const owner = MODULE_OWNED_FIELDS[key]
          if (owner) {
            moduleBuckets[owner] = moduleBuckets[owner] || {}
            moduleBuckets[owner][key] = value
          } else {
            agentDraft[key] = value
          }
        }
        for (const [owner, config] of Object.entries(moduleBuckets)) {
          // 🔗2 修复：模块配置接口按**扁平**对象处理（modules.py update_config 直接
          // 遍历 request.json()），不能再包一层 {config: {...}}，否则会往模块配置里
          // 写进垃圾键 "config"。agent 自身的键仍走 /api/agent/config 的 {config:...}。
          await http.post(
            `/api/module/${owner}/config`,
            config,
            { params: { account_id: targetBotId } },
          )
        }
        await http.post(
          '/api/agent/config',
          { config: agentDraft, permission: this.permission, enabled: this.enabled },
          { params: { account_id: targetBotId } },
        )
        // 保存期间用户又改了：保持 dirty，等下一轮
        if (this.editSeq !== snapshotSeq) return true
        this.dirty = false
        this.saveStatus = 'clean'
        const notify = useNotifyStore()
        notify.push('Agent 配置已保存', 'success')
        return true
      } catch (err) {
        if (this.editSeq !== snapshotSeq) return false
        this.saveStatus = 'error'
        const notify = useNotifyStore()
        notify.push(errorMessage(err), 'error')
        return false
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
