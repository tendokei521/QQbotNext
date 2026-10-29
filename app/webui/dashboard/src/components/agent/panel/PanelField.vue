<script setup lang="ts">
/**
 * 面板里的单个配置字段。
 *
 * 面板字段清单来自 ``ai-panel.json``，但字段类型仍以 schema 为准：
 * - ``boolean`` 用「左文字 + 右开关」的行式布局，与面板里的总开关形态一致；
 * - ``string_list`` 在 ConfigForm 里由 StringListWidget 承载，这里补上同样的分支，
 *   否则「触发关键词」会被渲染成一个普通文本框；
 * - 其它类型交给 ``FieldControl``（它自带 label/description 渲染）。
 */
import { computed } from 'vue'
import FieldControl from '@/components/config/FieldControl.vue'
import StringListWidget from '@/components/config/StringListWidget.vue'

const props = defineProps<{
  fieldKey: string
  schema: Record<string, any>
  value: any
  botId?: string | null
}>()

const emit = defineEmits<{
  (e: 'update', value: any): void
}>()

const isBool = computed(() => ['boolean', 'bool'].includes(String(props.schema?.type || '').toLowerCase()))
const isStringList = computed(() => String(props.schema?.type || '').toLowerCase() === 'string_list')
const listValue = computed<string[]>(() => (Array.isArray(props.value) ? (props.value as string[]) : []))
</script>

<template>
  <div class="panel-field">
    <div v-if="isBool" class="switch-row">
      <div>
        <div v-if="schema.label" class="switch-title">{{ schema.label }}</div>
        <div v-if="schema.description" class="switch-desc">{{ schema.description }}</div>
      </div>
      <v-switch
        :model-value="!!value"
        color="primary"
        inset
        hide-details
        @update:model-value="(v: any) => emit('update', !!v)"
      />
    </div>
    <template v-else-if="isStringList">
      <div v-if="schema.label" class="field-label">{{ schema.label }}</div>
      <div v-if="schema.description" class="field-desc">{{ schema.description }}</div>
      <StringListWidget :model-value="listValue" @update:model-value="(v: string[]) => emit('update', v)" />
    </template>
    <FieldControl
      v-else
      :field-key="fieldKey"
      :schema="schema"
      :value="value"
      :bot-id="botId ?? null"
      @update="(v: any) => emit('update', v)"
    />
  </div>
</template>

<style scoped>
.panel-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

/* 布尔开关：左文字 + 右开关，与面板总开关同一形态 */
.switch-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.switch-title {
  font-size: 14px;
  font-weight: 600;
}

.switch-desc {
  font-size: 12.5px;
  color: rgba(var(--v-theme-on-surface), 0.55);
  margin-top: 2px;
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
