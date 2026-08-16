<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  fieldKey: string
  schema: Record<string, any>
  value: any
}>()

const emit = defineEmits<{
  (e: 'update', value: any): void
}>()

const type = computed(() => String(props.schema.type || 'text').toLowerCase())
const isBool = computed(() => type.value === 'boolean' || type.value === 'bool')
const isNumber = computed(() => type.value === 'integer' || type.value === 'float' || type.value === 'number')
const isSelect = computed(() => type.value === 'select')
const isTextarea = computed(() => type.value === 'textarea')
const isTime = computed(() => type.value === 'time')
const isPassword = computed(() => type.value === 'password')

const numberAttrs = computed(() => {
  const attrs: Record<string, number | undefined> = {}
  if (props.schema.min !== undefined && props.schema.min !== null) attrs.min = Number(props.schema.min)
  if (props.schema.max !== undefined && props.schema.max !== null) attrs.max = Number(props.schema.max)
  if (props.schema.step !== undefined && props.schema.step !== null) attrs.step = Number(props.schema.step)
  return attrs
})

const selectOptions = computed(() => {
  const opts = props.schema.options
  if (!opts) return []
  // dict {value: label}（旧版） 或 [{value,label}] / string[]
  if (Array.isArray(opts)) {
    return opts.map((o) => (typeof o === 'string' ? { title: o, value: o } : { title: o.label, value: o.value }))
  }
  return Object.entries(opts).map(([value, label]) => ({ title: String(label), value }))
})

const numberValue = computed(() => {
  if (props.value === undefined || props.value === null || props.value === '') return undefined
  return type.value === 'integer' ? parseInt(String(props.value), 10) : parseFloat(String(props.value))
})

function onNumberInput(v: string | number | null) {
  if (v === null || v === '') {
    emit('update', type.value === 'integer' ? 0 : 0)
    return
  }
  const n = type.value === 'integer' ? parseInt(String(v), 10) : parseFloat(String(v))
  emit('update', Number.isNaN(n) ? 0 : n)
}

function onBoolInput(v: boolean | null) {
  emit('update', !!v)
}
</script>

<template>
  <div class="field-control">
    <div v-if="schema.label" class="field-label">{{ schema.label || fieldKey }}</div>
    <div v-if="schema.description" class="field-desc">{{ schema.description }}</div>

    <v-switch
      v-if="isBool"
      :model-value="!!value"
      color="primary"
      inset
      hide-details
      @update:model-value="onBoolInput"
    />

    <v-select
      v-else-if="isSelect"
      :model-value="value !== undefined && value !== null ? String(value) : undefined"
      :items="selectOptions"
      :placeholder="schema.placeholder"
      hide-details
      density="comfortable"
      @update:model-value="(v: any) => emit('update', v ?? '')"
    />

    <v-textarea
      v-else-if="isTextarea"
      :model-value="value !== undefined && value !== null ? String(value) : ''"
      :rows="schema.rows || 3"
      :placeholder="schema.placeholder"
      hide-details
      density="comfortable"
      auto-grow
      @update:model-value="(v: string) => emit('update', v)"
    />

    <v-text-field
      v-else-if="isTime"
      :model-value="value || '00:00'"
      type="time"
      hide-details
      density="comfortable"
      style="max-width: 160px"
      @update:model-value="(v: string) => emit('update', v)"
    />

    <v-text-field
      v-else-if="isNumber"
      :model-value="numberValue"
      type="number"
      v-bind="numberAttrs"
      hide-details
      density="comfortable"
      @update:model-value="onNumberInput"
    />

    <v-text-field
      v-else
      :model-value="value !== undefined && value !== null ? String(value) : ''"
      :type="isPassword ? 'password' : 'text'"
      :placeholder="schema.placeholder || (isPassword ? '••••••••' : '')"
      hide-details
      density="comfortable"
      @update:model-value="(v: string) => emit('update', v)"
    />
  </div>
</template>

<style scoped>
.field-control {
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
