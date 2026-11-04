import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'
import http, { errorMessage } from '@/api/http'
import { useBotsStore } from './bots'

export interface BundleIssue {
  level: 'error' | 'warn' | string
  code: string
  title: string
  detail?: string
  fix?: string
}

export interface BundleSecret {
  key: string
  label: string
  required: boolean
  hint?: string
}

export interface BundleModuleInfo {
  name: string
  version: string
  required: boolean
  bundled: boolean
  package_version: string
  package_size: number
  installed_version: string
  /** install=将安装 / upgrade=将升级 / skip=已满足 / missing=缺失 / config_only=只写配置 */
  action: 'install' | 'upgrade' | 'skip' | 'missing' | 'config_only' | string
  /** 目标环境缺失的第三方依赖（导入/应用时可勾选自动联网安装） */
  missing_requirements?: string[]
}

export interface BundleReport {
  ok: boolean
  bundle: { id: string; name: string; version: string; description?: string; author?: any }
  summary: {
    modules: number
    presets: number
    agent_keys: number
    files: number
    size: number
    secrets_required: number
    /** 代码随包的模块数（应用时自动安装） */
    modules_bundled?: number
    /** 应用时需要安装/升级的模块数 */
    modules_to_install?: number
  }
  /** 预检的模块明细（逐条展示名称/版本/动作/体积） */
  modules?: BundleModuleInfo[]
  /** 可勾选内容（导入预检 / 导出面板共用） */
  contents?: BundleContents
  /** 包内带可执行模块代码 → 导入方必须显式确认信任 */
  requires_code_consent?: boolean
  issues: BundleIssue[]
  secrets: BundleSecret[]
  manifest?: Record<string, any>
}

/** 一个可勾选的内容分类（persona / provider / agent / modules） */
export type BundleSectionKey = 'persona' | 'provider' | 'agent' | 'modules'

/** 导出面板 / 导入应用的内容明细 */
export interface ContentModule {
  name: string
  display_name?: string
  version?: string
  builtin?: boolean
  enabled?: boolean | null
  has_config?: boolean
  size?: number
  bundled?: boolean
  action?: string
  missing_requirements?: string[]
}

export interface BundleContents {
  persona: { available: boolean; name?: string; names?: string[]; fields?: string[] }
  provider: {
    available: boolean
    preset_name?: string
    provider?: string
    api_base?: string
    has_secret?: boolean
    models?: { model: string; enabled?: boolean }[]
  }
  agent: { available: boolean; key_count?: number }
  modules: ContentModule[]
}

/** 勾选结果：要处理哪些分类、要处理哪些插件 */
export interface BundleContentSelection {
  sections: string[]
  modules: string[]
}

export const BUNDLE_SECTIONS: { key: BundleSectionKey; label: string }[] = [
  { key: 'persona', label: '人格' },
  { key: 'provider', label: '模型配置' },
  { key: 'agent', label: 'AI 设置' },
  { key: 'modules', label: '插件' },
]

/** 默认勾选：导出默认不带插件代码；导入/应用默认全带。 */
export function defaultSelection(
  contents: BundleContents | null | undefined,
  mode: 'export' | 'import',
): BundleContentSelection {
  const sections: string[] = []
  if (contents?.persona?.available) sections.push('persona')
  if (contents?.provider?.available) sections.push('provider')
  if (contents?.agent?.available) sections.push('agent')
  const modules = contents?.modules || []
  if (mode === 'import' && modules.length) sections.push('modules')
  return {
    sections,
    modules: mode === 'import' ? modules.map((item) => item.name) : [],
  }
}

/** 一句话摘要：将导出/应用哪些内容。 */
export function selectionSummary(
  contents: BundleContents | null | undefined,
  selection: BundleContentSelection,
  mode: 'export' | 'import',
): string {
  const parts: string[] = []
  if (selection.sections.includes('persona') && contents?.persona?.available) {
    parts.push('人格')
  }
  if (selection.sections.includes('provider') && contents?.provider?.available) {
    parts.push('模型配置')
  }
  if (selection.sections.includes('agent') && contents?.agent?.available) {
    parts.push('AI 设置')
  }
  if (selection.sections.includes('modules')) {
    parts.push(`插件 ${selection.modules.length} 个`)
  }
  if (!parts.length) return '还没有勾选内容'
  return `${mode === 'export' ? '将导出' : '将应用'}：${parts.join('、')}`
}

/** 密钥声明属于哪个内容分类（界面按勾选隐藏不需要填的密钥）。 */
export function secretSection(key: string): string {
  if (key.startsWith('providers[') || key.startsWith('provider_models[')) return 'provider'
  if (key.startsWith('agent.')) return 'agent'
  if (key.startsWith('module:')) return 'modules'
  return 'other'
}

export interface InstalledBundle {
  id: string
  name: string
  version: string
  description?: string
  imported_at: number
  size: number
  file: string
  secrets?: BundleSecret[]
  /** 包含的模块名（卡片展示"含模块 N"） */
  modules?: string[]
  modules_bundled?: number
  modules_to_install?: number
  /** 缺第三方依赖的模块数（导入时预检结果；旧包可能没有） */
  modules_missing_deps?: number
  /** 哪些模块缺依赖（应用对话框提示用） */
  deps_missing_modules?: string[]
  /** 包内含可执行模块代码（导入需显式信任） */
  has_code?: boolean
  /** 可勾选内容（导入时记下，应用对话框据此展示勾选面板；旧包没有） */
  contents?: BundleContents
}

export interface ActivationRecord {
  id: string
  bundle_id: string
  account_id: string
  status: 'applying' | 'active' | 'failed' | 'rolled_back' | string
  started_at: number
  finished_at: number
  error?: string
}

/** 应用后自检结果（模块是否真的加载、账号引用是否落位） */
export interface BundleHealth {
  ok: boolean
  agent: { persona: boolean; provider_preset: boolean; provider_model: boolean }
  modules: { name: string; loaded: boolean; error: string }[]
}

/** 一次模块依赖安装的结果（应用回执里的 modules_deps） */
export interface DepsInstallResult {
  ok?: boolean
  installed?: string[]
  missing?: string[]
  error?: string
  hint?: string
  stderr?: string
  command?: string[]
  exit_code?: number
}

export interface ModuleDepsLog {
  module: string
  deps: DepsInstallResult
}

export interface ApplyResult {
  activation_id: string
  status: string
  plan: {
    bundle: { id: string; name: string; version: string }
    creates: { kind: string; name: string }[]
    updates: { target: string; keys: string[] }[]
    secrets_required: BundleSecret[]
    /** 应用时需要安装/升级的随包模块 */
    modules_to_install?: string[]
    /** 本次复用了哪个本机 Provider 预设的密钥 */
    reused_provider_preset_id?: string
  }
  /** 本次真正新装/升级的模块（回滚只卸载"新装"的） */
  modules_installed?: string[]
  modules_upgraded?: string[]
  /** 勾选「自动安装依赖」时：每个随包模块的 pip 安装结果 */
  modules_deps?: ModuleDepsLog[]
}

/** 可复用的本机 Provider 预设 */
export interface ReusePreset {
  id: string
  name: string
  provider?: string
  config?: Record<string, any>
}

function _triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}

function _filenameFrom(disposition: string | undefined, fallback: string): string {
  const match = /filename\*=UTF-8''([^;]+)/.exec(disposition || '')
  return match ? decodeURIComponent(match[1]) : match?.[1] || fallback
}

/**
 * 预设中心：校验 / 导入登记 / 列表 / 下载 / 从账号导出 / 应用 / 回滚 / 卸载。
 *
 * 导出会带：人格卡（含工具/技能/预设对话）、模型池全量、Agent 配置、模块参数与启停；
 * 第三方模块代码随包（`modules/*.zip`），应用时走既有 zip 安装通道自动安装。
 */
export const useBundlesStore = defineStore('bundles', () => {
  const bots = useBotsStore()
  const list = ref<InstalledBundle[]>([])
  const activations = ref<ActivationRecord[]>([])
  const loading = ref(false)
  const validating = ref(false)
  const importing = ref(false)
  const applying = ref(false)
  const error = ref('')
  /** 当前账号：激活记录/应用目标按账号区分（工件列表本身是全局的） */
  const accountId = computed(() => bots.currentAccountId)
  /** 激活记录请求序号：切号后丢弃旧账号的迟到响应 */
  let activationsSeq = 0

  async function load(): Promise<void> {
    loading.value = true
    try {
      const res = await http.get('/api/bundles')
      list.value = res.data?.bundles || []
    } catch (err) {
      error.value = errorMessage(err)
    } finally {
      loading.value = false
    }
  }

  async function validateFile(file: File): Promise<BundleReport> {
    validating.value = true
    try {
      const form = new FormData()
      form.append('file', file)
      const res = await http.post('/api/bundles/validate', form)
      return res.data as BundleReport
    } finally {
      validating.value = false
    }
  }

  async function importFile(
    file: File,
    acceptCode = false,
  ): Promise<BundleReport & { path?: string; file?: string }> {
    importing.value = true
    try {
      const form = new FormData()
      form.append('file', file)
      form.append('accept_code', acceptCode ? 'true' : 'false')
      const res = await http.post('/api/bundles/import', form)
      await load()
      return res.data
    } finally {
      importing.value = false
    }
  }

  /** 一键：导入 + 应用到账号（失败时后端返回 imported=true，工件已登记可重试） */
  async function importAndApply(
    file: File,
    accountId: string,
    secrets: Record<string, string>,
    acceptCode = false,
    reusePresetId = '',
    installDeps = false,
    depsMirror = '',
    selection?: BundleContentSelection,
  ): Promise<BundleReport & { apply?: ApplyResult; health?: BundleHealth }> {
    importing.value = true
    applying.value = true
    try {
      const form = new FormData()
      form.append('file', file)
      form.append('account_id', accountId)
      form.append('secrets', JSON.stringify(secrets || {}))
      form.append('accept_code', acceptCode ? 'true' : 'false')
      if (reusePresetId) form.append('reuse_provider_preset_id', reusePresetId)
      if (installDeps) form.append('install_deps', 'true')
      if (depsMirror.trim()) form.append('deps_mirror', depsMirror.trim())
      form.append('selection', JSON.stringify(selection || {}))
      const res = await http.post('/api/bundles/import-apply', form)
      await Promise.all([load(), loadActivations(accountId)])
      return res.data as BundleReport & { apply?: ApplyResult; health?: BundleHealth }
    } finally {
      importing.value = false
      applying.value = false
    }
  }

  async function download(id: string): Promise<void> {
    const res = await http.get(`/api/bundles/${encodeURIComponent(id)}/download`, {
      responseType: 'blob',
    })
    _triggerDownload(res.data as Blob, _filenameFrom(res.headers['content-disposition'], `${id}.zip`))
  }

  async function exportAccount(options: {
    id: string
    name: string
    version?: string
    description?: string
    /** 只打包这些内容分类（persona / provider / agent / modules） */
    sections: string[]
    /** 只打包这些模块；空数组 = 不带插件 */
    modules: string[]
  }): Promise<void> {
    const accountId = bots.currentAccountId
    if (!accountId) throw new Error('先选择一个账号')
    const params: Record<string, any> = {
      account_id: accountId,
      bundle_id: options.id,
      name: options.name,
      version: options.version || '1.0.0',
      description: options.description || '',
      sections: options.sections.join(','),
      modules: options.modules.join(','),
    }
    const res = await http.get('/api/bundles/export', {
      params,
      responseType: 'blob',
    })
    _triggerDownload(
      res.data as Blob,
      _filenameFrom(res.headers['content-disposition'], `${options.id}.zip`),
    )
  }

  /** 导出面板：可勾选内容（人格 / 模型配置 / AI 设置 / 插件明细） */
  async function loadExportOptions(): Promise<BundleContents> {
    const accountId = bots.currentAccountId
    const empty: BundleContents = {
      persona: { available: false, fields: [] },
      provider: { available: false, models: [] },
      agent: { available: false, key_count: 0 },
      modules: [],
    }
    if (!accountId) return empty
    const res = await http.get('/api/bundles/export-options', {
      params: { account_id: accountId },
    })
    return (res.data?.contents || empty) as BundleContents
  }

  async function loadActivations(scopeAccountId?: string | null): Promise<void> {
    const seq = ++activationsSeq
    const aid = scopeAccountId || bots.currentAccountId
    if (!aid) {
      activations.value = []
      return
    }
    try {
      const res = await http.get('/api/bundles/activations', { params: { account_id: aid } })
      // 加载期间切了账号：丢弃旧账号的激活记录，避免显示到新账号名下
      if (seq !== activationsSeq) return
      activations.value = res.data?.activations || []
    } catch (err) {
      if (seq !== activationsSeq) return
      error.value = errorMessage(err)
    }
  }

  // 切账号：激活记录是账号维度的——立即清空旧账号的记录并按新账号重拉
  // （BundlesPage 不必自己 watch：它读的就是这里的 activations）
  watch(
    () => bots.currentAccountId,
    () => {
      activations.value = []
      error.value = ''
      if (bots.currentAccountId) void loadActivations()
    },
  )

  /** 应用到账号：写账号配置（事务 + 快照；失败后端自动回滚） */
  async function applyBundle(
    id: string,
    accountId: string,
    secrets: Record<string, string>,
    reusePresetId = '',
    installDeps = false,
    depsMirror = '',
    selection?: BundleContentSelection,
  ): Promise<ApplyResult & { health?: BundleHealth }> {
    applying.value = true
    try {
      const res = await http.post(`/api/bundles/${encodeURIComponent(id)}/apply`, {
        account_id: accountId,
        secrets,
        ...(selection ? { sections: selection.sections, modules: selection.modules } : {}),
        ...(reusePresetId ? { reuse_provider_preset_id: reusePresetId } : {}),
        ...(installDeps ? { install_deps: true } : {}),
        ...(depsMirror.trim() ? { deps_mirror: depsMirror.trim() } : {}),
      })
      await Promise.all([load(), loadActivations(accountId)])
      return res.data as ApplyResult & { health?: BundleHealth }
    } finally {
      applying.value = false
    }
  }

  /** 可复用的本机 Provider 预设（一键场景：不想重填 Key 时选一个已连通的） */
  async function loadReusePresets(): Promise<ReusePreset[]> {
    const res = await http.get('/api/provider-presets')
    return (res.data?.presets || []) as ReusePreset[]
  }

  async function rollbackActivation(activationId: string): Promise<void> {
    await http.post(`/api/activations/${encodeURIComponent(activationId)}/rollback`)
    await loadActivations()
  }

  /** 卸载预设：回滚本账号的激活 + 清理记账；无引用时后端删除工件文件。
   *  ``keepModules=true``：只回滚配置，保留随包安装的模块。 */
  async function uninstallBundle(id: string, keepModules = false): Promise<void> {
    const accountId = bots.currentAccountId
    await http.post(
      `/api/bundles/${encodeURIComponent(id)}/uninstall`,
      { ...(accountId ? { account_id: accountId } : {}), keep_modules: keepModules },
    )
    await Promise.all([load(), loadActivations(accountId)])
  }

  return {
    list,
    activations,
    loading,
    validating,
    importing,
    applying,
    error,
    accountId,
    load,
    loadActivations,
    validateFile,
    importFile,
    importAndApply,
    applyBundle,
    rollbackActivation,
    uninstallBundle,
    download,
    exportAccount,
    loadExportOptions,
    loadReusePresets,
  }
})
