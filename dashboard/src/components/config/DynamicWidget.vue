<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import http, { errorMessage } from '@/api/http'
import FieldControl from './FieldControl.vue'
import StringListWidget from './StringListWidget.vue'

interface DynOption {
  value: string
  label: string
}

const props = defineProps<{
  moduleName: string
  schema: Record<string, any>
  modelValue: Record<string, Record<string, any>>
  selectedValue: string
  botId: number | null
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: Record<string, Record<string, any>>): void
  (e: 'update:selectedValue', value: string): void
}>()

const options = ref<DynOption[]>([])
const fields = ref<Record<string, any>[]>([])
const selected = ref(props.selectedValue || '')
const loadingOptions = ref(false)
const loadingFields = ref(false)
const error = ref('')
// 当前选中项的字段值草稿
const draft = reactive<Record<string, any>>({})
// 用户一旦编辑就不再被外部快照覆盖，避免输入过程中被重置
const touched = ref(false)

function emitValue() {
  touched.value = true
  const saved = { ...(props.modelValue || {}) }
  saved[selected.value] = { ...draft }
  emit('update:modelValue', saved)
  emit('update:selectedValue', selected.value)
}

async function loadOptions() {
  if (!props.botId) {
    error.value = '连接 Bot 后获取数据'
    return
  }
  loadingOptions.value = true
  error.value = ''
  try {
    const res = await http.get(`/api/module/${props.moduleName}/dynamic/${props.schema.endpoint}`, {
      params: { bot_id: props.botId },
    })
    const data = res.data
    if (!data?.ok) throw new Error(data?.message || '加载失败')
    options.value = data.options || []
    if (!selected.value && options.value.length) {
      selected.value = String(options.value[0].value)
    }
    if (selected.value) await loadFields()
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    loadingOptions.value = false
  }
}

async function loadFields() {
  if (!props.botId || !selected.value) return
  loadingFields.value = true
  try {
    const res = await http.get(
      `/api/module/${props.moduleName}/dynamic/${props.schema.endpoint}/${encodeURIComponent(selected.value)}`,
      { params: { bot_id: props.botId } },
    )
    const data = res.data
    if (!data?.ok) throw new Error(data?.message || '加载失败')
    fields.value = data.fields || []
    // 回填已存值
    const saved = (props.modelValue || {})[selected.value] || {}
    fields.value.forEach((f) => {
      draft[f.key] = saved[f.key] ?? f.default ?? (f.type === 'boolean' ? false : '')
    })
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    loadingFields.value = false
  }
}

function onSelect(v: string) {
  selected.value = v
  Object.keys(draft).forEach((k) => delete draft[k])
  loadFields()
  emitValue()
}

watch(() => props.botId, loadOptions)
watch(
  () => props.selectedValue,
  (v) => {
    if (v && v !== selected.value) selected.value = v
  },
)
watch(
  () => props.modelValue,
  (val) => {
    if (touched.value || !selected.value) return
    const saved = (val || {})[selected.value] || {}
    fields.value.forEach((f) => {
      draft[f.key] = saved[f.key] ?? f.default ?? (f.type === 'boolean' ? false : '')
    })
  },
  { deep: true },
)

if (props.botId) loadOptions()
</script>

<template>
  <div class="dynamic-widget">
    <div v-if="loadingOptions" class="hint"><v-progress-circular size="16" indeterminate /> 加载中…</div>
    <div v-else-if="error" class="hint error">{{ error }}</div>
    <template v-else>
      <v-select
        :model-value="selected || undefined"
        :items="options.map((o) => ({ title: o.label, value: o.value }))"
        label="选择配置项"
        density="comfortable"
        variant="outlined"
        hide-details
        class="mb-2"
        @update:model-value="onSelect"
      />
      <div v-if="loadingFields" class="hint"><v-progress-circular size="16" indeterminate /> 加载字段…</div>
      <div v-else class="fields-box">
        <template v-for="f in fields" :key="f.key">
          <StringListWidget
            v-if="String(f.type || '').toLowerCase() === 'string_list'"
            :model-value="(draft[f.key] as string[]) || []"
            @update:model-value="(v: string[]) => { draft[f.key] = v; emitValue() }"
          />
          <FieldControl
            v-else
            :field-key="f.key"
            :schema="f"
            :value="draft[f.key]"
            @update="(v: any) => { draft[f.key] = v; emitValue() }"
          />
        </template>
      </div>
    </template>
  </div>
</template>

<style scoped>
.dynamic-widget {
  display: flex;
  flex-direction: column;
  gap: 6px;
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

.fields-box {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 10px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  border-radius: 10px;
  background: rgba(var(--v-theme-on-surface), 0.02);
}
</style>
