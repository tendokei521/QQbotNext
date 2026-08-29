<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import http, { errorMessage } from '@/api/http'
import { useNotifyStore } from '@/stores/notify'

interface LogFileInfo {
  name: string
  size: number
  mtime: number
}

interface ArchiveInfo {
  folder: string
  files: LogFileInfo[]
}

interface ExportListResponse {
  ok: boolean
  logs_dir: string
  current: LogFileInfo[]
  archives: ArchiveInfo[]
}

interface LogFolder {
  key: string
  label: string
  files: LogFileInfo[]
}

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
}>()

const notify = useNotifyStore()
const loading = ref(false)
const downloadingZip = ref(false)
const list = ref<ExportListResponse>({ ok: true, logs_dir: '', current: [], archives: [] })
const selected = ref<string[]>([])
const expanded = ref<Record<string, boolean>>({})

const selectedCount = computed(() => selected.value.length)
const hasSelection = computed(() => selected.value.length > 0)

const folders = computed<LogFolder[]>(() => {
  const archiveFolders: LogFolder[] = [...list.value.archives]
    .sort((a, b) => (a.folder < b.folder ? 1 : -1))
    .map((a) => ({
      key: a.folder,
      label: a.folder,
      files: a.files,
    }))
  return [
    {
      key: 'current',
      label: '当前轮次',
      files: list.value.current,
    },
    ...archiveFolders,
  ]
})

function itemKey(folder: string, name: string): string {
  return `${folder}|${name}`
}

function parseKey(key: string): { folder: string; name: string } {
  const idx = key.indexOf('|')
  return { folder: idx === -1 ? '' : key.slice(0, idx), name: idx === -1 ? key : key.slice(idx + 1) }
}

function selectedItems() {
  return selected.value.map((key) => {
    const item = parseKey(key)
    return {
      // 前端“当前轮次”用 current 展示，后端用空字符串表示当前目录
      folder: item.folder === 'current' ? '' : item.folder,
      name: item.name,
    }
  })
}

function fmtSize(size: number): string {
  if (!size) return '0 B'
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

function fmtTime(ts: number): string {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false })
}

function isExpanded(key: string): boolean {
  return !!expanded.value[key]
}

function toggleFolder(key: string) {
  expanded.value[key] = !expanded.value[key]
}

function hasFile(folder: string, name: string): boolean {
  return selected.value.includes(itemKey(folder, name))
}

function toggleFile(folder: string, name: string) {
  const key = itemKey(folder, name)
  if (hasFile(folder, name)) {
    selected.value = selected.value.filter((k) => k !== key)
  } else {
    selected.value.push(key)
  }
}

function folderSelectedAll(folder: LogFolder): boolean {
  return folder.files.length > 0 && folder.files.every((f) => hasFile(folder.key, f.name))
}

function toggleFolderAll(folder: LogFolder) {
  const keys = folder.files.map((f) => itemKey(folder.key, f.name))
  if (folderSelectedAll(folder)) {
    selected.value = selected.value.filter((k) => !keys.includes(k))
  } else {
    selected.value = Array.from(new Set([...selected.value, ...keys]))
  }
}

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

async function loadList() {
  loading.value = true
  try {
    const res = await http.get<ExportListResponse>('/api/logs/export/list')
    list.value = res.data
    // 默认展开“当前轮次”，历史归档保持折叠
    expanded.value = { current: true }
    selected.value = []
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    loading.value = false
  }
}

async function downloadZip() {
  if (!hasSelection.value || downloadingZip.value) return
  downloadingZip.value = true
  try {
    const res = await http.post(
      '/api/logs/export/zip',
      { items: selectedItems() },
      { responseType: 'blob', timeout: 60000 },
    )
    const blob = res.data instanceof Blob ? res.data : new Blob([res.data])
    saveBlob(blob, `qqbot-logs-${new Date().toISOString().slice(0, 19).replace(/[-:T]/g, '')}.zip`)
    notify.push('日志打包下载已开始', 'success')
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    downloadingZip.value = false
  }
}

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    if (!list.value.current.length && !list.value.archives.length) {
      loadList()
    } else {
      // 重复打开也默认展开当前轮次
      expanded.value = { current: true }
    }
  },
)

onMounted(() => {
  if (props.modelValue) loadList()
})
</script>

<template>
  <v-dialog :model-value="props.modelValue" max-width="720" @update:model-value="emit('update:modelValue', $event)">
    <v-card class="log-export-card">
      <v-card-title class="d-flex align-center">
        <v-icon icon="mdi-file-export-outline" class="mr-2" color="primary" />
        导出日志
        <v-spacer />
        <v-btn icon="mdi-close" variant="text" size="small" @click="emit('update:modelValue', false)" />
      </v-card-title>

      <div class="log-export-body">
        <v-progress-linear v-if="loading" indeterminate color="primary" class="mb-2" />

        <div class="folder-scroll">
          <div v-for="folder in folders" :key="folder.key" class="folder-block">
            <div class="folder-row" @click="toggleFolder(folder.key)">
              <v-icon size="small" :icon="isExpanded(folder.key) ? 'mdi-chevron-down' : 'mdi-chevron-right'" />
              <span class="folder-label">{{ folder.label }}</span>
              <v-chip size="small" variant="tonal" class="ml-2">{{ folder.files.length }} 个文件</v-chip>
              <v-spacer />
              <v-btn
                size="small"
                variant="text"
                :disabled="!folder.files.length"
                @click.stop="toggleFolderAll(folder)"
              >
                {{ folderSelectedAll(folder) ? '取消全选' : '全选' }}
              </v-btn>
            </div>

            <div v-if="isExpanded(folder.key)" class="folder-files">
              <div
                v-for="f in folder.files"
                :key="itemKey(folder.key, f.name)"
                class="file-row"
                @click="toggleFile(folder.key, f.name)"
              >
                <div class="file-info">
                  <span class="file-name">{{ f.name }}</span>
                  <span class="file-meta">{{ fmtSize(f.size) }} · {{ fmtTime(f.mtime) }}</span>
                </div>
                <v-checkbox-btn
                  :model-value="hasFile(folder.key, f.name)"
                  color="primary"
                  @update:model-value="toggleFile(folder.key, f.name)"
                />
              </div>
              <div v-if="!folder.files.length" class="empty-files">
                该时段暂无日志文件
              </div>
            </div>
          </div>
        </div>
      </div>

      <v-card-actions class="download-bar">
        <span class="text-caption" style="opacity: 0.65">
          已选 {{ selectedCount }} 个文件
        </span>
        <v-spacer />
        <v-btn variant="text" @click="emit('update:modelValue', false)">取消</v-btn>
        <v-btn
          color="primary"
          variant="tonal"
          prepend-icon="mdi-zip-box-outline"
          :disabled="!hasSelection || downloadingZip"
          :loading="downloadingZip"
          @click="downloadZip"
        >
          下载 ZIP
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.log-export-card {
  display: flex;
  flex-direction: column;
  max-height: 80vh;
}

.log-export-body {
  flex: 1 1 auto;
  min-height: 0;
  padding: 0 24px;
  display: flex;
  flex-direction: column;
}

.folder-scroll {
  flex: 1 1 auto;
  min-height: 0;
  max-height: 52vh;
  overflow-y: auto;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  border-radius: 8px;
  padding: 4px;
}

.folder-block {
  margin-bottom: 2px;
}

.folder-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 10px;
  border-radius: 6px;
  cursor: pointer;
  user-select: none;
  transition: background-color 0.12s ease;
}

.folder-row:hover {
  background: rgba(var(--v-theme-primary), 0.06);
}

.folder-label {
  font-weight: 600;
  font-size: 16px;
}

.folder-files {
  padding: 4px 8px 10px 28px;
}

.file-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 6px;
  border-radius: 6px;
  cursor: pointer;
}

.file-row:hover {
  background: rgba(var(--v-theme-primary), 0.04);
}

.file-row :deep(.v-checkbox-btn) {
  margin-left: auto;
}

.file-info {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.file-name {
  font-size: 15px;
  font-weight: 500;
}

.file-meta {
  font-size: 13px;
  color: rgba(var(--v-theme-on-surface), 0.55);
}

.empty-files {
  padding: 8px 10px;
  font-size: 13px;
  color: rgba(var(--v-theme-on-surface), 0.5);
}

.download-bar {
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  flex-shrink: 0;
}
</style>
