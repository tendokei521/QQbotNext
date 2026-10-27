import { defineStore } from 'pinia'
import { ref } from 'vue'
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
  }
  issues: BundleIssue[]
  secrets: BundleSecret[]
  manifest?: Record<string, any>
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
 * 套装中心：校验 / 导入登记 / 列表 / 下载 / 从账号导出。
 *
 * P2 只做"工件"层——导入不会改账号配置（应用在 P3），store 也不假装它会。
 */
export const useBundlesStore = defineStore('bundles', () => {
  const bots = useBotsStore()
  const list = ref<InstalledBundle[]>([])
  const loading = ref(false)
  const validating = ref(false)
  const importing = ref(false)
  const error = ref('')

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

  async function importFile(file: File): Promise<BundleReport & { path?: string; file?: string }> {
    importing.value = true
    try {
      const form = new FormData()
      form.append('file', file)
      const res = await http.post('/api/bundles/import', form)
      await load()
      return res.data
    } finally {
      importing.value = false
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
  }): Promise<void> {
    const accountId = bots.currentAccountId
    if (!accountId) throw new Error('先选择一个账号')
    const res = await http.get('/api/bundles/export', {
      params: {
        account_id: accountId,
        bundle_id: options.id,
        name: options.name,
        version: options.version || '1.0.0',
        description: options.description || '',
      },
      responseType: 'blob',
    })
    _triggerDownload(
      res.data as Blob,
      _filenameFrom(res.headers['content-disposition'], `${options.id}.zip`),
    )
  }

  return {
    list,
    loading,
    validating,
    importing,
    error,
    load,
    validateFile,
    importFile,
    download,
    exportAccount,
  }
})
