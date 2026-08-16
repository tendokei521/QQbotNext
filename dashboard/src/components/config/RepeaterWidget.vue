<script setup lang="ts">
import { ref } from 'vue'
import FieldControl from './FieldControl.vue'

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

function templateKeys(): string[] {
  const tpl = props.schema.template || {}
  return Object.keys(tpl)
}

function add() {
  const values: Record<string, any> = {}
  const tpl = props.schema.template || {}
  Object.entries(tpl).forEach(([key, s]: [string, any]) => {
    values[key] = s.default ?? (s.type === 'boolean' ? false : '')
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
  emit(
    'update:modelValue',
    items.value.map((it) => ({ ...it.values })),
  )
}
</script>

<template>
  <div class="repeater-widget">
    <div v-for="(item, i) in items" :key="item.__id" class="repeater-card">
      <div class="card-head">
        <span class="card-title">分组 {{ i + 1 }}</span>
        <v-btn variant="text" icon="mdi-delete-outline" color="error" size="small" @click="remove(i)" />
      </div>
      <div class="card-body">
        <FieldControl
          v-for="key in templateKeys()"
          :key="key"
          :field-key="key"
          :schema="(schema.template || {})[key]"
          :value="item.values[key]"
          @update="(v: any) => onSub(i, key, v)"
        />
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
</style>
