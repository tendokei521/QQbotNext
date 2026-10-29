<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import http, { errorMessage } from '@/api/http'
import { useBotsStore } from '@/stores/bots'
import { useModulesStore } from '@/stores/modules'
import { useToolsStore } from '@/stores/tools'
import { useNotifyStore } from '@/stores/notify'
import { PERMISSION_LABELS, permissionLabel } from '@/agent/labels'
import ToolInfo from '@/components/tools/ToolInfo.vue'

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

const bots = useBotsStore()
const modules = useModulesStore()
const toolsStore = useToolsStore()
const notify = useNotifyStore()

/** OneBot 工具设置属于插件「工具扩展」，与「功能插件 → 工具扩展」是同一份配置 */
const TOOLS_MODULE = 'llm_tools_extra'
const moduleConfig = computed<Record<string, any>>(() => modules.modules[TOOLS_MODULE]?.config || {})
/** 插件列表接口给的是「已保存的原始配置」，没保存过的键要回退 schema 默认值 */
const toolSchemaItems = computed<Record<string, any>>(() => {
  const schema = modules.modules[TOOLS_MODULE]?.config_schema as { items?: Record<string, any> } | undefined
  return schema?.items || {}
})
const toolsConfig = computed<Record<string, any>>(() => {
  const defaults: Record<string, any> = {}
  for (const [key, field] of Object.entries(toolSchemaItems.value)) {
    if (field && typeof field === 'object' && 'default' in field) defaults[key] = field.default
  }
  return { ...defaults, ...moduleConfig.value }
})

/** 只提交改动的键：后端按 key 合并，不会覆盖插件的其它配置 */
async function saveToolsConfig(patch: Record<string, unknown>) {
  const current = moduleConfig.value
  modules.patch(TOOLS_MODULE, { config: { ...current, ...patch } })
  try {
    await modules.saveConfig(TOOLS_MODULE, patch)
  } catch (err) {
    notify.push(errorMessage(err), 'error')
    try {
      await modules.load()
    } catch {
      /* 回滚加载失败不再叠加提示 */
    }
  }
}

const tools = ref<OneBotTool[]>([])
const loading = ref(false)

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
  get: () => !!toolsConfig.value.onebot_tools_enable,
  set: (v: boolean) => void saveToolsConfig({ onebot_tools_enable: !!v }),
})

const denied = computed<string[]>(() =>
  Array.isArray(toolsConfig.value.onebot_tools_denied) ? toolsConfig.value.onebot_tools_denied : [],
)
const allowed = computed<string[]>(() =>
  Array.isArray(toolsConfig.value.onebot_tools_allowed) ? toolsConfig.value.onebot_tools_allowed : [],
)
const overrides = computed<Record<string, ToolOverride>>(() => toolsConfig.value.onebot_tool_overrides || {})

function toolPermission(tool: OneBotTool): string {
  return overrides.value[tool.name]?.permission || tool.base_permission || tool.permission
}

function toolScopes(tool: OneBotTool): string[] {
  return overrides.value[tool.name]?.scopes || tool.base_scopes || tool.scopes
}

function updateOverride(tool: OneBotTool, partial: ToolOverride) {
  const current = overrides.value[tool.name] || {}
  const next = { ...overrides.value, [tool.name]: { ...current, ...partial } }
  void saveToolsConfig({ onebot_tool_overrides: next })
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
    void saveToolsConfig({
      onebot_tools_denied: Array.from(new Set([...denied.value, name])),
      onebot_tools_allowed: allowed.value.filter((x) => x !== name),
    })
  } else {
    const patch: Record<string, unknown> = {
      onebot_tools_denied: denied.value.filter((x) => x !== name),
    }
    if (allowed.value.length) {
      patch.onebot_tools_allowed = Array.from(new Set([...allowed.value, name]))
    }
    void saveToolsConfig(patch)
  }
}

function resetToggles() {
  void saveToolsConfig({
    onebot_tools_denied: [],
    onebot_tools_allowed: [],
    onebot_tool_overrides: {},
  })
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

function riskLabel(risk: string): string {
  return risk === 'read' ? '只读' : risk === 'send' ? '消息' : '管理'
}

/** 触发权限可选项：与「回复与权限」共用同一套叫法；OneBot 侧只有这 5 档 */
const ONEBOT_PERMISSION_KEYS = ['everyone', 'member', 'group_admin', 'group_owner', 'owner']
const PERMISSION_ITEMS = ONEBOT_PERMISSION_KEYS.map((value) => ({
  value,
  title: PERMISSION_LABELS[value] || value,
}))

const SCOPE_LABELS: Record<string, string> = {
  '*': '全部',
  group: '群聊',
  private: '私聊',
}

/** 可用范围：后端给的是 `*` / `group` / `private`，界面上说人话。 */
function scopeLabels(scopes: string[]): string {
  return scopes.map((scope) => SCOPE_LABELS[scope] || scope).join(' / ')
}

async function loadTools() {
  if (!bots.currentAccountId) return
  loading.value = true
  try {
    const res = await http.get<{ ok: boolean; log_enabled: boolean; tools: OneBotTool[] }>('/api/agent/onebot/tools', {
      params: { account_id: bots.currentAccountId },
    })
    tools.value = res.data.tools || []
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    loading.value = false
  }
}

/** 开关数据来自插件配置：首次进入先确保模块列表（含 config）已加载 */
async function ensureModuleConfig() {
  if (modules.modules[TOOLS_MODULE]) return
  try {
    await modules.load()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
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
  await ensureModuleConfig()
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
    await ensureModuleConfig()
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
      <v-card-text class="text-caption" style="opacity: 0.65">
        开启后，模型可以主动发消息、查群信息等。敏感管理操作只建议给账号主人。
        <div class="mt-1">与「功能插件 → 工具扩展」是同一份设置，改这里等于改插件配置。</div>
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
                    <ToolInfo :tool="tool">
                      <template #title-extra>
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
                      </template>
                      <template #meta>
                        <v-chip size="small" variant="tonal" :color="riskColor(tool.risk)">{{ riskLabel(tool.risk) }}</v-chip>
                        <v-chip size="small" variant="tonal">权限：{{ permissionLabel(toolPermission(tool)) }}</v-chip>
                        <v-chip size="small" variant="tonal">可用范围：{{ scopeLabels(toolScopes(tool)) }}</v-chip>
                      </template>
                    </ToolInfo>
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
                    :items="PERMISSION_ITEMS"
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
              </div>
            </template>
          </template>
        </div>
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
