<script setup lang="ts">
/**
 * 单个工具行：名称 + 一行简描述（≤40 字）+ 右侧开关；长说明与参数收进「详情」。
 *
 * 四个来源页（系统 / OneBot / 外部 / 插件）共用，保证同一工具在哪都长一样。
 * 不展示工具内部 id：用户只需要知道它做什么、现在能不能用。
 */
import { computed, ref } from 'vue'
import { toolDisplayBrief, toolDisplayTitle } from './toolDisplay'

export interface ToolLike {
  name: string
  description?: string
  parameters?: Record<string, unknown>
  enabled?: boolean
  ready?: boolean
  effective?: boolean
  prerequisite?: string
  module?: string
}

const props = withDefaults(
  defineProps<{
    tool: ToolLike
    /** 整页总开关关闭时禁用本行开关 */
    disabled?: boolean
    switchLoading?: boolean
    showSwitch?: boolean
  }>(),
  { disabled: false, switchLoading: false, showSwitch: true },
)

const emit = defineEmits<{
  (e: 'toggle'): void
}>()

const expanded = ref(false)
const title = computed(() => toolDisplayTitle(props.tool))
const brief = computed(() => toolDisplayBrief(props.tool, 40))
/** 描述与名称相同（或只是重复前半句）时不显示第二行，避免「名称 + 同名描述」 */
const showBrief = computed(() => {
  const text = brief.value.trim()
  const name = title.value.trim()
  if (!text) return false
  if (text === name) return false
  return !(text.startsWith(name) && text.length <= name.length + 2)
})

const TYPE_LABELS: Record<string, string> = {
  string: '文本',
  integer: '整数',
  number: '数字',
  boolean: '开关',
  array: '列表',
  object: '对象',
}

const PARAM_LABELS: Record<string, string> = {
  group_id: '群号',
  user_id: 'QQ 号',
  message_id: '消息编号',
  message: '消息内容',
  text: '文本',
  no_cache: '是否跳过缓存',
  duration: '时长',
}

interface ParamRow {
  name: string
  label: string
  type: string
  required: boolean
  description: string
}

const paramRows = computed<ParamRow[]>(() => {
  const parameters = props.tool.parameters || {}
  const properties = parameters.properties
  if (!properties || typeof properties !== 'object') return []
  const required = Array.isArray(parameters.required) ? parameters.required.map(String) : []
  return Object.entries(properties as Record<string, any>).map(([name, raw]) => {
    const spec = (raw || {}) as Record<string, any>
    const type = typeof spec.type === 'string' ? spec.type : ''
    const description = typeof spec.description === 'string' ? spec.description : ''
    return {
      name,
      label: PARAM_LABELS[name] || '参数',
      type: TYPE_LABELS[type] || type || '不限',
      required: required.includes(name),
      description: description || '无说明',
    }
  })
})
</script>

<template>
  <div class="tool-item" :class="{ 'is-off': tool.effective === false }">
    <div class="tool-row">
      <div class="tool-main">
        <div class="tool-title-row">
          <span class="tool-title">{{ title }}</span>
          <slot name="badges" />
        </div>
        <div v-if="showBrief" class="tool-brief app-line-clamp-1">{{ brief }}</div>
        <div class="tool-meta">
          <span v-if="tool.ready === false" class="tool-warn">{{ tool.prerequisite || '当前不可用' }}</span>
          <slot name="meta" />
          <v-btn
            size="x-small"
            variant="text"
            class="tool-detail-btn"
            :append-icon="expanded ? 'mdi-chevron-up' : 'mdi-chevron-down'"
            @click.stop="expanded = !expanded"
          >
            {{ expanded ? '收起' : '详情' }}
          </v-btn>
        </div>
      </div>
      <v-switch
        v-if="showSwitch"
        class="tool-switch"
        :model-value="!!tool.enabled"
        :disabled="disabled || tool.ready === false"
        :loading="switchLoading"
        color="primary"
        density="compact"
        hide-details
        @click.stop
        @update:model-value="emit('toggle')"
      />
    </div>

    <div v-if="expanded" class="tool-detail" @click.stop>
      <div class="tool-detail-desc">{{ tool.description || '这个工具没有说明。' }}</div>
      <template v-if="paramRows.length">
        <div class="app-section-title mt-3 mb-1">参数</div>
        <div class="param-list">
          <div v-for="row in paramRows" :key="row.name" class="param-row">
            <span class="param-name">{{ row.label }}</span>
            <span class="param-type">{{ row.type }}<template v-if="row.required"> · 必填</template></span>
            <span class="param-desc">{{ row.description }}</span>
          </div>
        </div>
      </template>
      <slot name="detail" />
    </div>
  </div>
</template>

<style scoped>
.tool-item {
  border: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  border-radius: 10px;
  padding: 12px 14px;
  transition: opacity 0.12s ease;
}

.tool-item.is-off {
  opacity: 0.6;
}

.tool-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.tool-main {
  flex: 1 1 auto;
  min-width: 0;
}

.tool-title-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px 8px;
}

.tool-title {
  font-weight: 600;
  font-size: 14.5px;
  overflow-wrap: anywhere;
}

.tool-brief {
  margin-top: 2px;
  font-size: 12.5px;
  line-height: 1.5;
  color: rgba(var(--v-theme-on-surface), 0.62);
}

.tool-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px 10px;
  margin-top: 4px;
}

.tool-warn {
  font-size: 12px;
  color: rgb(var(--v-theme-warning));
}

.tool-detail-btn {
  margin-inline-start: -6px;
}

.tool-switch {
  flex: 0 0 auto;
  margin: 0;
}

.tool-detail {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.08);
}

.tool-detail-desc {
  font-size: 13px;
  line-height: 1.7;
  color: rgba(var(--v-theme-on-surface), 0.72);
  overflow-wrap: anywhere;
}

.param-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.param-row {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 12.5px;
}

.param-name {
  font-weight: 600;
}

.param-type {
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.param-desc {
  color: rgba(var(--v-theme-on-surface), 0.72);
}
</style>
