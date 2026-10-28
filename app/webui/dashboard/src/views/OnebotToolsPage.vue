<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import http, { errorMessage } from '@/api/http'
import ConfigForm from '@/components/config/ConfigForm.vue'
import { useAgentConfigStore } from '@/stores/agentConfig'
import { useBotsStore } from '@/stores/bots'
import { useToolsStore } from '@/stores/tools'
import { useNotifyStore } from '@/stores/notify'
import { agentSectionById } from '@/agent/sections'
import { buildSectionSchema } from '@/utils/schema'

interface OneBotTool {
  name: string
  description: string
  parameters: Record<string, any>
  risk: 'read' | 'send' | 'admin'
  permission: string
  scopes: string[]
  base_permission: string
  base_scopes: string[]
  sensitivity: 'normal' | 'high' | 'critical'
  category: string
  doc_url: string
  enabled: boolean
  blocked: boolean
  blocked_reason: string
  warning: string
}

interface ToolOverride {
  permission?: string
  scopes?: string[]
}

const agent = useAgentConfigStore()
const bots = useBotsStore()
const toolsStore = useToolsStore()
const notify = useNotifyStore()
// OneBot 字段由 registry 的「工具与能力」页 adv_onebot 子分组声明；
// 本页只渲染那一块，避免把 Tavily/知识库等其它字段也搬进工具管理页。
const onebotSpec = agentSectionById('adv_tools')?.groups.find((g) => g.id === 'adv_onebot')
const schema = computed(() => (onebotSpec ? buildSectionSchema(agent.schema, [onebotSpec]) : {}))

const tools = ref<OneBotTool[]>([])
const loading = ref(false)
const detailKey = ref('')

const DOC_CATEGORY_ORDER = [
  '流式传输扩展',
  '流式接口',
  '群组扩展',
  '核心接口',
  '群组接口',
  '扩展接口',
  '系统接口',
  '用户接口',
  '系统扩展',
  '消息扩展',
  '文件接口',
  'Go-CQHTTP',
  '消息接口',
  '文件扩展',
  '用户扩展',
  '频道接口',
  'AI 扩展',
]

const toolGroups = computed(() => {
  const map = new Map<string, OneBotTool[]>()
  for (const tool of tools.value) {
    const category = tool.category || '未分类'
    if (!map.has(category)) map.set(category, [])
    map.get(category)!.push(tool)
  }
  return Array.from(map.entries())
    .sort((a, b) => {
      const ia = DOC_CATEGORY_ORDER.indexOf(a[0])
      const ib = DOC_CATEGORY_ORDER.indexOf(b[0])
      return (ia === -1 ? 999 : ia) - (ib === -1 ? 999 : ib)
    })
    .map(([category, items]) => ({ category, items }))
})

const enabled = computed<boolean>({
  get: () => !!agent.draft.onebot_tools_enable,
  set: (v: boolean) => agent.onChange('onebot_tools_enable', v),
})

const logEnabled = computed<boolean>({
  get: () => !!agent.draft.onebot_tools_log_enabled,
  set: (v: boolean) => agent.onChange('onebot_tools_log_enabled', !!v),
})

const denied = computed<string[]>(() => Array.isArray(agent.draft.onebot_tools_denied) ? agent.draft.onebot_tools_denied : [])
const allowed = computed<string[]>(() => Array.isArray(agent.draft.onebot_tools_allowed) ? agent.draft.onebot_tools_allowed : [])
const overrides = computed<Record<string, ToolOverride>>(() => agent.draft.onebot_tool_overrides || {})

function toolPermission(tool: OneBotTool): string {
  return overrides.value[tool.name]?.permission || tool.base_permission || tool.permission
}

function toolScopes(tool: OneBotTool): string[] {
  return overrides.value[tool.name]?.scopes || tool.base_scopes || tool.scopes
}

function updateOverride(tool: OneBotTool, partial: ToolOverride) {
  const current = overrides.value[tool.name] || {}
  const next = { ...overrides.value, [tool.name]: { ...current, ...partial } }
  agent.onChange('onebot_tool_overrides', next)
}

function setToolPermission(tool: OneBotTool, permission: string) {
  updateOverride(tool, { permission })
}

function isSensitiveWide(tool: OneBotTool): boolean {
  if (!['high', 'critical'].includes(tool.sensitivity)) return false
  return ['everyone', 'member'].includes(toolPermission(tool))
}

function isToolOn(name: string): boolean {
  if (denied.value.includes(name)) return false
  if (allowed.value.length && !allowed.value.includes(name)) return false
  return true
}

function toggleTool(name: string) {
  if (isToolOn(name)) {
    agent.onChange('onebot_tools_denied', Array.from(new Set([...denied.value, name])))
    agent.onChange('onebot_tools_allowed', allowed.value.filter((x) => x !== name))
  } else {
    agent.onChange('onebot_tools_denied', denied.value.filter((x) => x !== name))
    if (allowed.value.length) {
      agent.onChange('onebot_tools_allowed', Array.from(new Set([...allowed.value, name])))
    }
  }
}

function resetToggles() {
  agent.onChange('onebot_tools_denied', [])
  agent.onChange('onebot_tools_allowed', [])
  agent.onChange('onebot_tool_overrides', {})
}

function isCollapsed(category: string): boolean {
  return !!toolsStore.collapsedOf('onebot')[category]
}

function toggleCategory(category: string) {
  const next = { ...toolsStore.collapsedOf('onebot'), [category]: !isCollapsed(category) }
  toolsStore.setCollapsed('onebot', next)
}

function riskColor(risk: string): string {
  return risk === 'read' ? 'success' : risk === 'send' ? 'info' : 'warning'
}

function permissionLabel(permission: string): string {
  if (permission === 'everyone') return '所有人'
  if (permission === 'group_admin') return '群管理'
  if (permission === 'group_owner') return '群主'
  if (permission === 'owner') return '账号主人'
  return '成员'
}

function riskLabel(risk: string): string {
  return risk === 'read' ? '只读' : risk === 'send' ? '消息' : '管理'
}

const SCOPE_LABELS: Record<string, string> = {
  '*': '全部',
  group: '群聊',
  private: '私聊',
}

const PARAM_TYPE_LABELS: Record<string, string> = {
  string: '文本',
  integer: '整数',
  number: '数字',
  boolean: '开关',
  array: '列表',
  object: '对象',
}

interface ParamRow {
  name: string
  type: string
  required: boolean
  description: string
}

/** 可用范围：后端给的是 `*` / `group` / `private`，界面上说人话。 */
function scopeLabels(scopes: string[]): string {
  return scopes.map((scope) => SCOPE_LABELS[scope] || scope).join(' / ')
}

/** 把 parameters 的 JSON Schema 拆成"参数名 + 类型 + 是否必填 + 说明"。 */
function paramRows(tool: OneBotTool): ParamRow[] {
  const properties = tool.parameters?.properties
  if (!properties || typeof properties !== 'object') return []
  const required = Array.isArray(tool.parameters?.required) ? tool.parameters.required : []
  return Object.entries(properties as Record<string, any>).map(([name, raw]) => {
    const meta = (raw || {}) as Record<string, any>
    const type = typeof meta.type === 'string' ? meta.type : ''
    const description = typeof meta.description === 'string' ? meta.description : ''
    return {
      name,
      type: PARAM_TYPE_LABELS[type] || type || '不限',
      required: required.includes(name),
      description: description || '无说明',
    }
  })
}

async function loadTools() {
  if (!agent.botId) return
  loading.value = true
  try {
    const res = await http.get<{ ok: boolean; log_enabled: boolean; tools: OneBotTool[] }>('/api/agent/onebot/tools', {
      params: { account_id: agent.botId },
    })
    tools.value = res.data.tools || []
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    loading.value = false
  }
}

function restoreScroll() {
  const top = toolsStore.scrollTop.onebot || 0
  if (top) window.scrollTo(0, top)
}

function saveScroll() {
  toolsStore.setScroll('onebot', window.scrollY || 0)
}

onMounted(async () => {
  await agent.load()
  loadTools()
  restoreScroll()
  window.addEventListener('scroll', saveScroll, { passive: true })
})

onUnmounted(() => {
  saveScroll()
  window.removeEventListener('scroll', saveScroll)
})

watch(
  () => bots.currentAccountId,
  async () => {
    await agent.load(true)
    loadTools()
  },
)
</script>

<template>
  <div>
    <v-card variant="outlined" class="mb-4">
      <v-card-title class="d-flex align-center">
        <v-icon icon="mdi-power" class="mr-2" color="teal" /> 总开关
        <v-spacer />
        <v-switch v-model="enabled" color="primary" density="compact" hide-details />
      </v-card-title>
      <v-card-text class="text-caption" style="opacity: 0.65; display: flex; flex-wrap: wrap; gap: 16px">
        <span>开启后，模型可以主动发消息、查群信息等。敏感管理操作只建议给账号主人。</span>
        <v-switch
          v-model="logEnabled"
          label="输出普通调用日志"
          color="primary"
          density="compact"
          hide-details
        />
        <v-switch
          :model-value="!!agent.draft.onebot_tools_debug"
          label="详细调试日志（排查问题时开）"
          color="warning"
          density="compact"
          hide-details
          @update:model-value="(v: any) => agent.onChange('onebot_tools_debug', !!v)"
        />
      </v-card-text>
    </v-card>

    <v-card variant="outlined" class="mb-4">
      <v-card-title class="d-flex align-center">
        <v-icon icon="mdi-robot-industrial" class="mr-2" color="teal" /> 可用工具
        <v-spacer />
        <v-btn
          size="small"
          variant="outlined"
          prepend-icon="mdi-open-in-new"
          href="https://napcat.apifox.cn/"
          target="_blank"
          rel="noopener"
        >
          接口文档
        </v-btn>
        <v-btn size="small" variant="tonal" :disabled="!enabled" @click="resetToggles">恢复默认</v-btn>
      </v-card-title>
      <v-card-text>
        <v-progress-linear v-if="loading" indeterminate color="primary" />

        <div class="tool-list">
          <template v-for="group in toolGroups" :key="group.category">
            <div
              class="tool-category"
              role="button"
              tabindex="0"
              :aria-expanded="!isCollapsed(group.category)"
              @click="toggleCategory(group.category)"
              @keydown.enter.prevent="toggleCategory(group.category)"
              @keydown.space.prevent="toggleCategory(group.category)"
            >
              <v-icon :icon="isCollapsed(group.category) ? 'mdi-chevron-down' : 'mdi-chevron-up'" size="small" />
              <span class="tool-category-name">{{ group.category }}</span>
              <span class="tool-category-count">{{ group.items.length }}</span>
            </div>
            <template v-if="!isCollapsed(group.category)">
              <div
                v-for="tool in group.items"
                :key="tool.name"
                class="tool-item"
                :class="{ 'is-off': !enabled || !isToolOn(tool.name) }"
              >
                <div class="tool-row">
                  <div class="tool-info">
                    <div class="tool-name">
                      {{ tool.name }}
                      <a
                        v-if="tool.doc_url"
                        :href="tool.doc_url"
                        target="_blank"
                        rel="noopener"
                        class="tool-doc-link"
                        @click.stop
                      >
                        <v-icon icon="mdi-open-in-new" size="x-small" />
                        文档
                      </a>
                    </div>
                    <div class="tool-desc">{{ tool.description }}</div>
                    <div class="tool-meta d-flex gap-3 flex-wrap">
                      <v-chip size="small" variant="tonal" :color="riskColor(tool.risk)">{{ riskLabel(tool.risk) }}</v-chip>
                      <v-chip size="small" variant="tonal">权限：{{ permissionLabel(toolPermission(tool)) }}</v-chip>
                      <v-chip size="small" variant="tonal">可用范围：{{ scopeLabels(toolScopes(tool)) }}</v-chip>
                    </div>
                  </div>
                  <v-switch
                    :model-value="enabled && isToolOn(tool.name)"
                    :disabled="!enabled"
                    color="primary"
                    density="compact"
                    hide-details
                    @update:model-value="toggleTool(tool.name)"
                  />
                </div>
                <div class="tool-policy" :class="{ 'is-disabled': !enabled }">
                  <v-select
                    :model-value="toolPermission(tool)"
                    :disabled="!enabled"
                    :items="[
                      { title: '所有人', value: 'everyone' },
                      { title: '成员及以上', value: 'member' },
                      { title: '群管理及以上', value: 'group_admin' },
                      { title: '群主', value: 'group_owner' },
                      { title: '账号主人', value: 'owner' },
                    ]"
                    label="触发权限"
                    variant="outlined"
                    density="compact"
                    hide-details
                    @update:model-value="(v: any) => setToolPermission(tool, v)"
                  />
                  <div class="tool-scope-static">
                    <span class="text-caption">可用范围：</span>
                    <span class="font-weight-medium">{{ scopeLabels(toolScopes(tool)) }}</span>
                  </div>
                  <v-alert v-if="isSensitiveWide(tool)" type="warning" variant="tonal" density="compact" class="mt-2">
                    {{ tool.name }} 属于敏感工具。默认只给「{{ permissionLabel(tool.base_permission) }}」，现已放宽到「{{ permissionLabel(toolPermission(tool)) }}」。
                  </v-alert>
                </div>
                <div v-if="detailKey === tool.name" class="tool-detail">
                  <div class="text-caption mb-1">参数说明</div>
                  <div v-if="paramRows(tool).length" class="param-list">
                    <div v-for="row in paramRows(tool)" :key="row.name" class="param-row">
                      <span class="param-name">{{ row.name }}</span>
                      <span class="param-type">{{ row.type }}<template v-if="row.required"> · 必填</template></span>
                      <span class="param-desc">{{ row.description }}</span>
                    </div>
                  </div>
                  <div v-else class="text-caption">这个工具不需要参数。</div>
                  <v-expansion-panels variant="accordion" flat class="param-raw">
                    <v-expansion-panel title="查看原始参数">
                      <v-expansion-panel-text>
                        <pre class="tool-pre">{{ JSON.stringify(tool.parameters, null, 2) }}</pre>
                      </v-expansion-panel-text>
                    </v-expansion-panel>
                  </v-expansion-panels>
                </div>
                <v-btn v-else size="x-small" variant="text" prepend-icon="mdi-format-list-bulleted" @click="detailKey = tool.name">
                  查看参数
                </v-btn>
              </div>
            </template>
          </template>
        </div>
      </v-card-text>
    </v-card>

    <v-card variant="outlined" class="mb-4">
      <v-card-title class="d-flex align-center">
        <v-icon icon="mdi-tune" class="mr-2" color="teal" /> 高级配置
      </v-card-title>
      <v-card-text>
        <ConfigForm :module-name="'agent'" :schema="schema" :config="agent.draft" :bot-id="agent.botId" @change="agent.onChange" />
      </v-card-text>
    </v-card>
  </div>
</template>

<style scoped>
.tool-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.tool-category {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
  padding: 4px 2px;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.1);
  cursor: pointer;
  user-select: none;
}

.tool-category:first-child {
  margin-top: 0;
}

/* 键盘聚焦时才出现的焦点环，不改变常态视觉 */
.tool-category:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: 2px;
  border-radius: 6px;
}

.tool-category-name {
  font-weight: 700;
  font-size: 15px;
  color: rgba(var(--v-theme-on-surface), 0.85);
}

.tool-category-count {
  font-size: 12px;
  color: rgba(var(--v-theme-on-surface), 0.55);
  background: rgba(var(--v-theme-on-surface), 0.08);
  border-radius: 999px;
  padding: 1px 8px;
}

.tool-doc-link {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  margin-left: 8px;
  font-size: 12px;
  color: rgb(var(--v-theme-primary));
  text-decoration: none;
  vertical-align: middle;
}

.tool-doc-link:hover {
  text-decoration: underline;
}

.tool-item {
  border: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  border-radius: 10px;
  padding: 12px;
  transition: opacity 0.12s ease;
}

.tool-item.is-off {
  opacity: 0.55;
}

.tool-row {
  display: flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
}

.tool-info {
  flex: 1 1 auto;
  min-width: 0;
}

.tool-name {
  font-weight: 600;
  font-size: 16px;
}

.tool-desc {
  font-size: 14px;
  color: rgba(var(--v-theme-on-surface), 0.65);
  margin: 2px 0 4px;
}

.tool-meta {
  align-items: center;
}

.tool-policy {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed rgba(var(--v-theme-on-surface), 0.1);
}

.tool-policy.is-disabled {
  opacity: 0.55;
}

.tool-policy :deep(.v-select) {
  max-width: 180px;
}

.tool-scope-static {
  font-size: 14px;
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.tool-detail {
  margin-top: 10px;
  border-top: 1px dashed rgba(var(--v-theme-on-surface), 0.12);
  padding-top: 10px;
}

.param-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
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
  font-family: monospace;
  font-weight: 600;
}

.param-type {
  font-size: 12px;
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.param-desc {
  color: rgba(var(--v-theme-on-surface), 0.75);
}

.param-raw {
  margin-top: 4px;
}

.tool-pre {
  background: rgba(var(--v-theme-on-surface), 0.04);
  border-radius: 8px;
  padding: 10px;
  font-size: 12px;
  max-height: 220px;
  overflow: auto;
  white-space: pre-wrap;
}
</style>
