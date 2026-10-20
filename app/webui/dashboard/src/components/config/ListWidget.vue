<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import http, { errorMessage } from '@/api/http'

interface ListRow {
  id: string
  name: string
  meta: any[]
  enabled: boolean
  index: number
}

const props = defineProps<{
  moduleName: string
  schema: Record<string, any>
  modelValue: Record<string, { enabled: boolean; index: number }>
  modeValue?: string
  botId: number | null
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: Record<string, { enabled: boolean; index: number }>): void
  (e: 'update:modeValue', value: string): void
}>()

const rows = ref<ListRow[]>([])
const mode = ref<string>(props.modeValue || 'all')
const loading = ref(false)
const error = ref('')
const dragIndex = ref<number | null>(null)
// 用户一旦编辑就不再被外部快照覆盖，避免操作过程中被重置
const interacted = ref(false)
const sortable = computed(() => !!props.schema.sortable)
const checkboxes = computed(() => !!props.schema.checkboxes)
const modeSelect = computed(() => !!props.schema.mode_select)
const idField = computed(() => props.schema.id_field || 'id')
const nameField = computed(() => props.schema.name_field || 'name')
const metaFields = computed<string[]>(() => props.schema.meta_fields || [])

function emitValue() {
  interacted.value = true
  const out: Record<string, { enabled: boolean; index: number }> = {}
  rows.value.forEach((r, i) => {
    out[r.id] = { enabled: r.enabled, index: i }
  })
  emit('update:modelValue', out)
  emit('update:modeValue', mode.value)
}

async function load() {
  if (!props.botId) {
    rows.value = []
    error.value = '连接 Bot 后获取数据'
    return
  }
  loading.value = true
  error.value = ''
  try {
    const res = await http.get(`/api/module/${props.moduleName}/list/${props.schema.endpoint}`, {
      params: { bot_id: props.botId },
    })
    const data = res.data
    if (!data?.ok) throw new Error(data?.message || '加载失败')
    mode.value = data.mode || 'all'
    rows.value = (data.items || []).map((item: any) => ({
      id: String(item.id ?? item[idField.value] ?? ''),
      name: String(item.name ?? item[nameField.value] ?? item.id ?? ''),
      meta: Array.isArray(item.meta) ? item.meta : metaFields.value.map((f) => item.meta?.[f] ?? item[f]),
      enabled: item.enabled ?? true,
      index: item.index ?? 0,
    }))
    // 与已存配置合并（enabled/index 以已存为准）
    if (props.modelValue) {
      rows.value.forEach((r) => {
        const saved = props.modelValue[r.id]
        if (saved) {
          r.enabled = saved.enabled
          r.index = saved.index
        }
      })
    }
    rows.value.sort((a, b) => a.index - b.index)
    applyMode(mode.value, true)
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}

function applyMode(m: string, quiet = false) {
  mode.value = m
  if (m === 'all') rows.value.forEach((r) => (r.enabled = true))
  else if (m === 'none') rows.value.forEach((r) => (r.enabled = false))
  if (!quiet) emitValue()
}

function onCheckbox(row: ListRow, v: boolean | null) {
  row.enabled = !!v
  if (mode.value !== 'partial') mode.value = 'partial'
  emitValue()
}

function onDragStart(i: number) {
  dragIndex.value = i
}

function onDrop(i: number) {
  const from = dragIndex.value
  dragIndex.value = null
  if (from === null || from === i) return
  const [moved] = rows.value.splice(from, 1)
  rows.value.splice(i, 0, moved)
  if (mode.value !== 'partial') mode.value = 'partial'
  emitValue()
}

watch(() => props.botId, load)
watch(
  () => props.modeValue,
  (v) => {
    if (v && v !== mode.value) mode.value = v
  },
)
watch(
  () => props.modelValue,
  (val) => {
    if (interacted.value || rows.value.length === 0) return
    rows.value.forEach((r) => {
      const saved = (val || {})[r.id]
      if (saved) {
        r.enabled = saved.enabled
        r.index = saved.index
      }
    })
    rows.value.sort((a, b) => a.index - b.index)
  },
  { deep: true },
)

if (props.botId) load()
</script>

<template>
  <div class="list-widget">
    <div v-if="loading" class="hint"><v-progress-circular size="16" indeterminate /> 加载中…</div>
    <div v-else-if="error" class="hint error">{{ error }}</div>
    <template v-else>
      <v-select
        v-if="modeSelect"
        :model-value="mode"
        :items="[
          { title: '全部启用', value: 'all' },
          { title: '按勾选', value: 'partial' },
          { title: '全部禁用', value: 'none' },
        ]"
        density="compact"
        variant="outlined"
        hide-details
        class="mode-select"
        @update:model-value="(v: string) => applyMode(v)"
      />
      <div
        v-for="(row, i) in rows"
        :key="row.id"
        class="list-row"
        :draggable="sortable"
        @dragstart="onDragStart(i)"
        @dragover.prevent
        @drop.prevent="onDrop(i)"
      >
        <v-icon v-if="sortable" icon="mdi-drag-vertical" class="drag-icon" size="small" />
        <v-checkbox
          v-if="checkboxes"
          :model-value="row.enabled"
          density="compact"
          hide-details
          color="primary"
          @update:model-value="(v: boolean | null) => onCheckbox(row, v)"
        />
        <div class="row-main">
          <div class="row-name">{{ row.name }}</div>
          <div v-if="row.meta.length" class="row-meta">{{ row.meta.filter((m) => m !== undefined && m !== null).join(' · ') }}</div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.list-widget {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.hint {
  font-size: 12.5px;
  color: rgba(var(--v-theme-on-surface), 0.5);
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 0;
}

.hint.error {
  color: rgb(var(--v-theme-error));
}

.mode-select {
  max-width: 200px;
  margin-bottom: 6px;
}

.list-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 8px;
  border-radius: 8px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  background: rgb(var(--v-theme-surface));
}

.list-row[draggable='true'] {
  cursor: grab;
}

.list-row[draggable='true']:active {
  cursor: grabbing;
}

.drag-icon {
  color: rgba(var(--v-theme-on-surface), 0.35);
}

.row-main {
  min-width: 0;
}

.row-name {
  font-size: 13.5px;
  font-weight: 500;
}

.row-meta {
  font-size: 12px;
  color: rgba(var(--v-theme-on-surface), 0.55);
}
</style>
