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
  /** 包内带可执行模块代码 → 导入方必须显式确认信任 */
  requires_code_consent?: boolean
  issues: BundleIssue[]
  secrets: BundleSecret[]
  manifest?: Record<string, any>
}

/** 导出对话框的模块选项 */
export interface ExportModuleOption {
  name: string
  display_name: string
  version: string
  builtin: boolean
  enabled: boolean
  has_config: boolean
  size: number
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
  /** 包内含可执行模块代码（导入需显式信任） */
  has_code?: boolean
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
    /** 只打包这些模块；不传 = 全部 */
    modules?: string[]
  }): Promise<void> {
    const accountId = bots.currentAccountId
    if (!accountId) throw new Error('先选择一个账号')
    const params: Record<string, any> = {
      account_id: accountId,
      bundle_id: options.id,
      name: options.name,
      version: options.version || '1.0.0',
      description: options.description || '',
    }
    if (options.modules) params.modules = options.modules.join(',')
    const res = await http.get('/api/bundles/export', {
      params,
      responseType: 'blob',
    })
    _triggerDownload(
      res.data as Blob,
      _filenameFrom(res.headers['content-disposition'], `${options.id}.zip`),
    )
  }

  /** 导出对话框：可选模块清单（含体积） */
  async function loadExportOptions(): Promise<ExportModuleOption[]> {
    const accountId = bots.currentAccountId
    if (!accountId) return []
    const res = await http.get('/api/bundles/export-options', {
      params: { account_id: accountId },
    })
    return (res.data?.modules || []) as ExportModuleOption[]
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
  ): Promise<ApplyResult & { health?: BundleHealth }> {
    applying.value = true
    try {
      const res = await http.post(`/api/bundles/${encodeURIComponent(id)}/apply`, {
        account_id: accountId,
        secrets,
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
