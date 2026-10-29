<script setup lang="ts">
/**
 * 工具信息块：中文动作名做主标题，原名降为副标题；描述折两行，参数收进详情弹窗。
 *
 * 四个工具页（系统 / OneBot / 外部 / 插件）共用，避免同名工具在不同页呈现不一致。
 * 额外徽标/链接通过 `meta` 插槽传入。
 */
import { computed, ref } from 'vue'
import { toolDisplayRest, toolDisplayTitle } from './toolDisplay'

const props = defineProps<{
  tool: {
    name: string
    description?: string
    parameters?: Record<string, any>
  }
}>()

const detailOpen = ref(false)

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
  type: string
  required: boolean
  description: string
}

/** 主标题：中文短名（description 首个分句，≤18 字）；没有描述时退回工具名 */
const actionTitle = computed(() => toolDisplayTitle(props.tool, 18))
/** 标题已经用掉了描述的第一分句，正文只显示剩下的部分，避免同一句话出现两遍 */
const descRest = computed(() => toolDisplayRest(props.tool))

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
      type: TYPE_LABELS[type] || type || '不限',
      required: required.includes(name),
      description: description || '无说明',
    }
  })
})

const rawParameters = computed(() => {
  try {
    return JSON.stringify(props.tool.parameters || {}, null, 2)
  } catch {
    return '{}'
  }
})

function paramLabel(name: string): string {
  return PARAM_LABELS[name] || name
}
</script>

<template>
  <div class="tool-info">
    <div class="tool-head">
      <span class="tool-title">{{ actionTitle }}</span>
      <span class="tool-raw-name" :title="`工具标识：${tool.name}`">{{ tool.name }}</span>
      <slot name="title-extra" />
    </div>
    <div v-if="descRest" class="tool-desc">{{ descRest }}</div>
    <div class="d-flex align-center flex-wrap gap-2 mt-1">
      <slot name="meta" />
      <v-btn size="x-small" variant="text" append-icon="mdi-chevron-right" @click.stop="detailOpen = true">
        详情
      </v-btn>
    </div>

    <v-dialog v-model="detailOpen" max-width="640" scrollable>
      <v-card>
        <v-card-title class="d-flex align-center flex-wrap gap-2">
          <v-icon icon="mdi-wrench-outline" class="mr-1" color="primary" />
          {{ actionTitle }}
          <v-spacer />
          <v-btn icon="mdi-close" variant="text" size="small" @click="detailOpen = false" />
        </v-card-title>
        <v-card-text>
          <div class="text-caption mb-3" style="opacity: 0.7">工具标识：{{ tool.name }}</div>
          <div class="text-body-2 mb-4">{{ tool.description || '这个工具没有说明。' }}</div>

          <div class="text-subtitle-2 mb-2">参数说明</div>
          <div v-if="paramRows.length" class="param-list">
            <div v-for="row in paramRows" :key="row.name" class="param-row">
              <span class="param-name">{{ paramLabel(row.name) }}</span>
              <span class="param-raw">{{ row.name }}</span>
              <span class="param-type">{{ row.type }}<template v-if="row.required"> · 必填</template></span>
              <span class="param-desc">{{ row.description }}</span>
            </div>
          </div>
          <div v-else class="text-caption mb-3">这个工具不需要参数。</div>

          <v-expansion-panels variant="accordion" flat class="mt-2">
            <v-expansion-panel title="高级：查看原始参数">
              <v-expansion-panel-text>
                <pre class="raw-params">{{ rawParameters }}</pre>
              </v-expansion-panel-text>
            </v-expansion-panel>
          </v-expansion-panels>
        </v-card-text>
      </v-card>
    </v-dialog>
  </div>
</template>

<style scoped>
.tool-head {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 4px 10px;
}

.tool-title {
  font-weight: 600;
  font-size: 16px;
  overflow-wrap: anywhere;
}

.tool-raw-name {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 11px;
  color: rgba(var(--v-theme-on-surface), 0.4);
  cursor: help;
  padding-left: 2px;
}

.tool-desc {
  font-size: 14px;
  color: rgba(var(--v-theme-on-surface), 0.65);
  margin: 2px 0 4px;
  line-height: 1.45;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  max-height: 2.9em;
  overflow-wrap: anywhere;
}

.param-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 8px;
}

.param-row {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 13px;
}

.param-name {
  font-weight: 600;
}

.param-raw {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 11px;
  color: rgba(var(--v-theme-on-surface), 0.45);
}

.param-type {
  font-size: 12px;
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.param-desc {
  color: rgba(var(--v-theme-on-surface), 0.75);
}

.raw-params {
  background: rgba(var(--v-theme-on-surface), 0.04);
  border-radius: 8px;
  padding: 8px 10px;
  font-size: 12px;
  max-height: 240px;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
