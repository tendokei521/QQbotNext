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

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
}>()

const notify = useNotifyStore()
const loading = ref(false)
const downloadingZip = ref(false)
const downloadingFiles = ref(false)
const list = ref<ExportListResponse>({ ok: true, logs_dir: '', current: [], archives: [] })
const selected = ref<string[]>([])

const selectedCount = computed(() => selected.value.length)
const hasSelection = computed(() => selected.value.length > 0)

function itemKey(folder: string, name: string): string {
  return `${folder}|${name}`
}

function parseKey(key: string): { folder: string; name: string } {
  const idx = key.indexOf('|')
  return { folder: idx === -1 ? '' : key.slice(0, idx), name: idx === -1 ? key : key.slice(idx + 1) }
}

function selectedItems() {
  return selected.value.map(parseKey)
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

function selectCurrentAll() {
  const keys = list.value.current.map((f) => itemKey('', f.name))
  selected.value = Array.from(new Set([...selected.value, ...keys]))
}

function clearCurrent() {
  const keys = list.value.current.map((f) => itemKey('', f.name))
  selected.value = selected.value.filter((k) => !keys.includes(k))
}

function selectArchiveAll(folder: string) {
  const archive = list.value.archives.find((a) => a.folder === folder)
  if (!archive) return
  const keys = archive.files.map((f) => itemKey(folder, f.name))
  selected.value = Array.from(new Set([...selected.value, ...keys]))
}

function clearArchive(folder: string) {
  const archive = list.value.archives.find((a) => a.folder === folder)
  if (!archive) return
  const keys = archive.files.map((f) => itemKey(folder, f.name))
  selected.value = selected.value.filter((k) => !keys.includes(k))
}

function allCurrentSelected(): boolean {
  return list.value.current.length > 0 && list.value.current.every((f) => hasFile('', f.name))
}

function allArchiveSelected(folder: string): boolean {
  const archive = list.value.archives.find((a) => a.folder === folder)
  return !!archive && archive.files.length > 0 && archive.files.every((f) => hasFile(folder, f.name))
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
    // 默认全选当前轮次
    const currentKeys = list.value.current.map((f) => itemKey('', f.name))
    selected.value = Array.from(new Set([...selected.value, ...currentKeys]))
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

async function downloadSingle(item: { folder: string; name: string }) {
  const params: Record<string, string> = { file: item.name }
  if (item.folder) params.folder = item.folder
  const res = await http.get('/api/logs/export/download', { params, responseType: 'blob' })
  const filename = item.folder ? `${item.folder}_${item.name}` : item.name
  const blob = res.data instanceof Blob ? res.data : new Blob([res.data])
  saveBlob(blob, filename)
}

async function downloadSelectedIndividually() {
  if (!hasSelection.value || downloadingFiles.value) return
  downloadingFiles.value = true
  try {
    const items = selectedItems()
    for (const item of items) {
      await downloadSingle(item)
      await new Promise((r) => setTimeout(r, 300))
    }
    notify.push('独立文件下载已开始', 'success')
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    downloadingFiles.value = false
  }
}

watch(
  () => props.modelValue,
  (open) => {
    if (open && !list.value.current.length && !list.value.archives.length) loadList()
  },
)

onMounted(() => {
  if (props.modelValue) loadList()
})
</script>

<template>
  <v-dialog :model-value="props.modelValue" max-width="720" @update:model-value="emit('update:modelValue', $event)">
    <v-card>
      <v-card-title class="d-flex align-center">
        <v-icon icon="mdi-file-export-outline" class="mr-2" color="primary" />
        导出日志
        <v-spacer />
        <v-btn icon="mdi-close" variant="text" size="small" @click="emit('update:modelValue', false)" />
      </v-card-title>

      <v-card-text>
        <v-progress-linear v-if="loading" indeterminate color="primary" class="mb-3" />

        <!-- 当前轮次 -->
        <div class="log-group">
          <div class="log-group-head">
            <v-icon icon="mdi-clock-outline" size="small" /> 当前轮次
            <v-spacer />
            <v-btn v-if="!allCurrentSelected()" size="x-small" variant="text" @click="selectCurrentAll">全选</v-btn>
            <v-btn v-else size="x-small" variant="text" @click="clearCurrent">取消全选</v-btn>
          </div>
          <v-list density="compact" class="log-file-list">
            <v-list-item v-for="f in list.current" :key="itemKey('', f.name)">
              <template #prepend>
                <v-checkbox-btn
                  :model-value="hasFile('', f.name)"
                  color="primary"
                  density="compact"
                  @update:model-value="toggleFile('', f.name)"
                />
              </template>
              <v-list-item-title>{{ f.name }}</v-list-item-title>
              <v-list-item-subtitle>{{ fmtSize(f.size) }} · {{ fmtTime(f.mtime) }}</v-list-item-subtitle>
            </v-list-item>
          </v-list>
        </div>

        <!-- 历史归档 -->
        <div class="log-group">
          <div class="log-group-head">
            <v-icon icon="mdi-archive-outline" size="small" /> 历史归档（6 小时轮转）
          </div>
          <div v-for="arch in list.archives" :key="arch.folder" class="archive-block">
            <div class="archive-head">
              <v-icon icon="mdi-folder-outline" size="small" /> {{ arch.folder }}
              <v-spacer />
              <v-btn v-if="!allArchiveSelected(arch.folder)" size="x-small" variant="text" @click="selectArchiveAll(arch.folder)">全选</v-btn>
              <v-btn v-else size="x-small" variant="text" @click="clearArchive(arch.folder)">取消全选</v-btn>
            </div>
            <v-list density="compact" class="log-file-list">
              <v-list-item v-for="f in arch.files" :key="itemKey(arch.folder, f.name)">
                <template #prepend>
                  <v-checkbox-btn
                    :model-value="hasFile(arch.folder, f.name)"
                    color="primary"
                    density="compact"
                    @update:model-value="toggleFile(arch.folder, f.name)"
                  />
                </template>
                <v-list-item-title>{{ f.name }}</v-list-item-title>
                <v-list-item-subtitle>{{ fmtSize(f.size) }} · {{ fmtTime(f.mtime) }}</v-list-item-subtitle>
              </v-list-item>
            </v-list>
          </div>
          <div v-if="!list.archives.length" class="text-caption pa-2" style="opacity: 0.55">
            暂无历史归档日志
          </div>
        </div>

        <!-- 中间：独立文件下载 -->
        <div class="middle-download">
          <div class="text-subtitle-2 mb-1">下载方式</div>
          <div class="d-flex align-center flex-wrap gap-3">
            <v-btn
              variant="outlined"
              color="primary"
              prepend-icon="mdi-file-download-outline"
              :disabled="!hasSelection || downloadingFiles"
              :loading="downloadingFiles"
              @click="downloadSelectedIndividually"
            >
              下载独立文件
            </v-btn>
            <div class="text-caption" style="opacity: 0.6">
              逐个下载所选日志，文件名保持原样
            </div>
          </div>
        </div>
      </v-card-text>

      <v-card-actions class="app-card-actions">
        <span class="text-caption mr-auto" style="opacity: 0.65">
          已选 {{ selectedCount }} 个文件
        </span>
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
.log-group {
  margin-bottom: 16px;
}

.log-group-head,
.archive-head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  font-size: 13.5px;
  padding: 4px 0;
}

.archive-block {
  border-left: 3px solid rgba(var(--v-theme-primary), 0.25);
  padding-left: 8px;
  margin-bottom: 10px;
}

.log-file-list {
  max-height: 240px;
  overflow-y: auto;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  border-radius: 8px;
}

.middle-download {
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  padding: 12px 0;
  margin: 12px 0;
}
</style>
