<script setup lang="ts">
import { ref, watch } from 'vue'
import FieldControl from './FieldControl.vue'
import StringListWidget from './StringListWidget.vue'

const props = defineProps<{
  schema: Record<string, any>
  modelValue: Record<string, any>[]
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: Record<string, any>[]): void
}>()

interface RepeaterItem {
  __id: number
  values: Record<string, any>
}

let seq = 0
const items = ref<RepeaterItem[]>(
  (props.modelValue ?? []).map((v) => ({ __id: ++seq, values: { ...v } })),
)
// 用户一旦编辑就不再被外部快照覆盖，避免输入过程中被重置
const touched = ref(false)

function templateKeys(): string[] {
  const tpl = props.schema.template || {}
  return Object.keys(tpl)
}

function add() {
  const values: Record<string, any> = {}
  const tpl = props.schema.template || {}
  Object.entries(tpl).forEach(([key, s]: [string, any]) => {
    if (String(s.type || '').toLowerCase() === 'string_list') {
      values[key] = Array.isArray(s.default) ? [...s.default] : []
    } else {
      values[key] = s.default ?? (s.type === 'boolean' ? false : '')
    }
  })
  items.value.push({ __id: ++seq, values })
  emitValue()
}

function remove(i: number) {
  items.value.splice(i, 1)
  emitValue()
}

function onSub(i: number, key: string, v: any) {
  items.value[i].values[key] = v
  emitValue()
}

function emitValue() {
  touched.value = true
  emit(
    'update:modelValue',
    items.value.map((it) => ({ ...it.values })),
  )
}

// 外部配置（如切账号 / WS 同步）到达时，若用户尚未编辑则同步渲染
watch(
  () => props.modelValue,
  (val) => {
    if (!touched.value) {
      items.value = (val ?? []).map((v) => ({ __id: ++seq, values: { ...v } }))
    }
  },
  { deep: true },
)
</script>

<template>
  <div class="repeater-widget">
    <div v-for="(item, i) in items" :key="item.__id" class="repeater-card">
      <div class="card-head">
        <span class="card-title">分组 {{ i + 1 }}</span>
        <v-btn variant="text" icon="mdi-delete-outline" color="error" size="small" @click="remove(i)" />
      </div>
      <div class="card-body">
        <template v-for="key in templateKeys()" :key="key">
          <div
            v-if="String((schema.template || {})[key]?.type || '').toLowerCase() === 'string_list'"
            class="repeater-subfield"
          >
            <div v-if="(schema.template || {})[key]?.label" class="field-label">
              {{ (schema.template || {})[key].label }}
            </div>
            <div v-if="(schema.template || {})[key]?.description" class="field-desc">
              {{ (schema.template || {})[key].description }}
            </div>
            <StringListWidget
              :model-value="(item.values[key] as string[]) || []"
              @update:model-value="(v: string[]) => onSub(i, key, v)"
            />
          </div>
          <FieldControl
            v-else
            :field-key="key"
            :schema="(schema.template || {})[key]"
            :value="item.values[key]"
            @update="(v: any) => onSub(i, key, v)"
          />
        </template>
      </div>
    </div>
    <v-btn size="small" variant="tonal" prepend-icon="mdi-plus" class="align-self-start" @click="add">
      新增分组
    </v-btn>
  </div>
</template>

<style scoped>
.repeater-widget {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.repeater-card {
  border: 1px solid rgba(var(--v-theme-on-surface), 0.1);
  border-radius: 12px;
  overflow: hidden;
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 12px;
  background: rgba(var(--v-theme-primary), 0.06);
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.08);
}

.card-title {
  font-size: 13px;
  font-weight: 600;
}

.card-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 12px;
}

.repeater-subfield {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.field-label {
  font-size: 13.5px;
  font-weight: 500;
}

.field-desc {
  font-size: 12px;
  color: rgba(var(--v-theme-on-surface), 0.55);
  line-height: 1.5;
  white-space: pre-wrap;
}
</style>
