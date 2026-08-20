<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import http, { errorMessage } from '@/api/http'
import { useBotsStore } from '@/stores/bots'
import { useNotifyStore } from '@/stores/notify'
import type { PermissionConfig } from '@/stores/modules'
import ConfigForm from '@/components/config/ConfigForm.vue'
import PermissionEditor from '@/components/config/PermissionEditor.vue'
import AgentPanels from '@/components/agent/AgentPanels.vue'
import { filterSchemaExcludeGroup } from '@/utils/schema'

const bots = useBotsStore()
const notify = useNotifyStore()

const botId = ref<number | null>(null)
const enabled = ref(false)
const schema = ref<Record<string, any>>({})
const route = useRoute()
const activeGroup = ref('')

function scrollToSection(section: string) {
  if (!section) return
  if (section.startsWith('group-')) activeGroup.value = section.slice('group-'.length)
  nextTick(() => {
    const el = document.getElementById(section)
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  })
}

function handleSectionQuery() {
  scrollToSection(String(route.query.section || ''))
}

watch(
  () => route.query.section,
  () => handleSectionQuery(),
)
const providerModels = ref<{ id: string; preset_id: string; preset_name?: string; model: string }[]>([])
const draft = reactive<Record<string, any>>({})
const poolModelIds = ref<string[]>([])
const poolDialog = ref(false)
const pendingPoolModelId = ref('')
const availablePoolModels = computed(() => providerModels.value.filter((m) => !poolModelIds.value.includes(m.id)))

function poolLabel(id: string): string {
  const m = providerModels.value.find((x) => x.id === id)
  return m ? `${m.preset_name || m.preset_id} / ${m.model}` : id
}

function syncPool() {
  draft.provider_model_pool = [...poolModelIds.value]
  onChange('provider_model_pool', [...poolModelIds.value])
}

function addPoolModel() {
  if (!pendingPoolModelId.value || poolModelIds.value.includes(pendingPoolModelId.value)) return
  poolModelIds.value.push(pendingPoolModelId.value)
  syncPool()
  poolDialog.value = false
  pendingPoolModelId.value = ''
}

function removePoolModel(index: number) {
  poolModelIds.value.splice(index, 1)
  syncPool()
}

function movePoolModel(index: number, delta: number) {
  const target = index + delta
  if (target < 0 || target >= poolModelIds.value.length) return
  const [moved] = poolModelIds.value.splice(index, 1)
  poolModelIds.value.splice(target, 0, moved)
  syncPool()
}
const permission = ref<PermissionConfig>({
  group_mode: 'blacklist',
  group_list: [],
  user_mode: 'blacklist',
  user_list: [],
})
const loading = ref(false)
const saveStatus = ref<'clean' | 'dirty' | 'saving' | 'error'>('clean')
let autosaveTimer: number | null = null
let dirtyFlag = false
// 待保存的目标 bot：自动保存触发时确定，避免切换到新 Bot 后把旧草稿写进新 Bot
let pendingBotId: number | null = null
let editSeq = 0

async function load() {
  // 先取消挂起的自动保存：切换账号/重新加载时丢弃未保存编辑，防止跨 Bot 误写
  if (autosaveTimer) {
    clearTimeout(autosaveTimer)
    autosaveTimer = null
  }
  // 刷新/首次进入时机器人列表可能还没加载：先补齐并恢复上次选择，避免误报“请先选择账号”
  if (!bots.bots.length) {
    try {
      await bots.fetchBots()
    } catch {
      /* 列表加载失败时走下面的空态 */
    }
  }
  if (bots.currentIndex === null && bots.bots.length) {
    bots.restoreSelection()
  }
  botId.value = bots.currentBot?.bot_id ?? null
  if (botId.value === null) {
    return
  }
  loading.value = true
  try {
    const res = await http.get<{
      ok: boolean
      bot_id: number | null
      enabled: boolean
      permission: PermissionConfig
      config: Record<string, any>
      schema: Record<string, any>
      provider_presets: { id: string; name: string }[]
      provider_models: { id: string; preset_id: string; preset_name?: string; model: string }[]
    }>('/api/agent/config', { params: { bot_id: botId.value } })
    const data = res.data
    enabled.value = !!data.enabled
    schema.value = filterSchemaExcludeGroup(data.schema || {}, 'group_memory')
    providerModels.value = data.provider_models || []
    Object.keys(draft).forEach((k) => delete draft[k])
    Object.assign(draft, data.config || {})
    const storedPool = Array.isArray(draft.provider_model_pool) ? draft.provider_model_pool : []
    const legacyPool = [
      draft.provider_model_id,
      ...(Array.isArray(draft.fallback_model_ids) ? draft.fallback_model_ids : []),
    ].filter(Boolean)
    poolModelIds.value = (storedPool.length ? storedPool : legacyPool).map(String)
    draft.provider_model_pool = [...poolModelIds.value]
    permission.value = {
      group_mode: data.permission?.group_mode || 'blacklist',
      group_list: [...(data.permission?.group_list || [])],
      user_mode: data.permission?.user_mode || 'blacklist',
      user_list: [...(data.permission?.user_list || [])],
    }
    saveStatus.value = 'clean'
    dirtyFlag = false
    handleSectionQuery()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    loading.value = false
  }
}

function scheduleAgentSave() {
  pendingBotId = botId.value
  dirtyFlag = true
  editSeq += 1
  saveStatus.value = 'dirty'
  if (autosaveTimer) clearTimeout(autosaveTimer)
  autosaveTimer = window.setTimeout(doSave, 2000)
}

function onChange(key: string, value: any) {
  draft[key] = value
  scheduleAgentSave()
}

function onPermissionChange(v: PermissionConfig) {
  permission.value = v
  scheduleAgentSave()
}

function onEnabledChange(v: boolean | null) {
  enabled.value = !!v
  scheduleAgentSave()
}

async function doSave() {
  if (autosaveTimer) {
    clearTimeout(autosaveTimer)
    autosaveTimer = null
  }
  if (!dirtyFlag) return
  const targetBotId = pendingBotId ?? botId.value
  const snapshotSeq = editSeq
  pendingBotId = null
  saveStatus.value = 'saving'
  try {
    await http.post(
      '/api/agent/config',
      { config: { ...draft }, permission: permission.value, enabled: enabled.value },
      { params: { bot_id: targetBotId } },
    )
    // 已切换到其它 Bot：该保存属于旧 Bot，不推进当前编辑状态（load 会重建草稿）
    if (targetBotId !== botId.value) return
    if (editSeq !== snapshotSeq) return // 保存期间又有新编辑：保留 dirty，交由下一次保存
    dirtyFlag = false
    saveStatus.value = 'clean'
    notify.push('Agent 配置已保存', 'success')
  } catch (err) {
    if (editSeq !== snapshotSeq) return
    saveStatus.value = 'error'
    notify.push(errorMessage(err), 'error')
  }
}

watch(
  () => bots.currentBot?.bot_id,
  () => load(),
)

onMounted(load)

onUnmounted(() => {
  if (autosaveTimer) {
    clearTimeout(autosaveTimer)
    autosaveTimer = null
  }
})
</script>

<template>
  <div>
    <div class="app-page-header" style="align-items: center">
      <div class="d-flex align-center gap-2 flex-wrap">
        <h1 class="app-page-title">Agent 面板</h1>
        <v-chip size="small" variant="tonal" color="primary">LLM · 框架级</v-chip>
        <v-chip v-if="saveStatus === 'saving'" size="small" color="primary" variant="flat">
          <v-progress-circular size="12" indeterminate class="mr-1" /> 保存中…
        </v-chip>
        <v-chip v-else-if="saveStatus === 'error'" size="small" color="error" variant="flat">保存失败</v-chip>
        <v-chip v-else-if="saveStatus === 'dirty'" size="small" color="warning" variant="flat">未保存</v-chip>
      </div>
      <div class="d-flex align-center gap-3">
        <v-switch :model-value="enabled" color="primary" label="启用 Agent" density="compact" hide-details @update:model-value="onEnabledChange" />
      </div>
    </div>
    <div class="app-page-subtitle">框架级 LLM Agent：配置、权限、定时任务与主动消息</div>

    <div v-if="botId === null" class="empty-tip">
      <v-icon icon="mdi-robot-off-outline" size="56" color="rgba(var(--v-theme-on-surface), 0.3)" />
      <div>请先在顶栏选择并连接一个 Bot，再配置 Agent</div>
      <v-btn variant="tonal" prepend-icon="mdi-refresh" class="mt-2" @click="load">重试</v-btn>
    </div>

    <template v-else>
      <v-progress-linear v-if="loading" indeterminate color="primary" />

      <v-card id="sec-permission" variant="outlined" class="mb-4">
          <v-card-title class="d-flex align-center">
            <v-icon icon="mdi-shield-account-outline" class="mr-2" color="primary" /> 响应范围控制
          </v-card-title>
          <v-card-text>
            <PermissionEditor :model-value="permission" @update:model-value="onPermissionChange" />
          </v-card-text>
        </v-card>

        <v-card id="sec-config" variant="outlined" class="mb-4">
          <v-card-title class="d-flex align-center">
            <v-icon icon="mdi-cogs" class="mr-2" color="primary" /> Agent 配置
            <v-spacer />
            <v-chip
              v-if="poolModelIds.length"
              size="small"
              variant="tonal"
              color="primary"
              class="mr-1"
            >
              {{ poolModelIds.length }} 个模型 · 按顺序请求
            </v-chip>
            <v-btn size="small" variant="tonal" prepend-icon="mdi-api" class="mr-1" @click="$router.push('/provider-presets')">
              管理预设
            </v-btn>
            <v-btn size="small" color="primary" variant="tonal" prepend-icon="mdi-content-save" :loading="saveStatus === 'saving'" @click="doSave">
              保存配置
            </v-btn>
          </v-card-title>
          <v-card-text>
            <div id="sec-models" class="pool-block mb-4">
              <div class="pool-head d-flex align-center mb-1">
                <v-icon icon="mdi-format-list-numbered" class="mr-1" color="primary" size="small" />
                <span class="font-weight-medium">Provider 模型（按顺序请求，从上到下依次尝试）</span>
                <v-spacer />
                <v-btn size="small" color="primary" variant="tonal" prepend-icon="mdi-plus" @click="pendingPoolModelId = ''; poolDialog = true">
                  添加模型
                </v-btn>
              </div>
              <v-list v-if="poolModelIds.length" density="compact">
                <v-list-item v-for="(id, i) in poolModelIds" :key="id">
                  <template #prepend>
                    <span class="pool-order">{{ i + 1 }}</span>
                  </template>
                  <v-list-item-title>{{ poolLabel(id) }}</v-list-item-title>
                  <template #append>
                    <v-btn size="x-small" variant="text" icon="mdi-arrow-up" :disabled="i === 0" @click="movePoolModel(i, -1)" />
                    <v-btn size="x-small" variant="text" icon="mdi-arrow-down" :disabled="i === poolModelIds.length - 1" @click="movePoolModel(i, 1)" />
                    <v-btn size="x-small" variant="text" icon="mdi-close" color="error" @click="removePoolModel(i)" />
                  </template>
                </v-list-item>
              </v-list>
              <div v-else class="text-caption text-center pa-4" style="color: rgba(var(--v-theme-on-surface), 0.45)">
                尚未配置模型，请添加一个 Provider 模型
              </div>
            </div>

            <v-divider class="mb-4" />

            <ConfigForm
              :module-name="'agent'"
              :schema="schema"
              :config="draft"
              :bot-id="botId"
              :active-group="activeGroup"
              @change="onChange"
            />
          </v-card-text>
        </v-card>

        <div id="sec-agent-panels">
          <AgentPanels :bot-id="botId" />
        </div>

      <v-dialog v-model="poolDialog" max-width="420">
        <v-card>
          <v-card-title class="d-flex align-center">
            <v-icon icon="mdi-plus" class="mr-2" color="primary" /> 添加 Provider 模型
          </v-card-title>
          <v-card-text>
            <v-select
              v-model="pendingPoolModelId"
              :items="availablePoolModels.map((m) => ({ title: `${m.preset_name || m.preset_id} / ${m.model}`, value: m.id }))"
              label="选择模型"
              density="comfortable"
              hide-details
            />
            <div v-if="!availablePoolModels.length" class="text-caption mt-2" style="color: rgba(var(--v-theme-on-surface), 0.55)">
              没有可添加的模型，请先到「Provider 预设」页配置连接与模型
            </div>
          </v-card-text>
          <v-card-actions>
            <v-spacer />
            <v-btn variant="text" @click="poolDialog = false">取消</v-btn>
            <v-btn color="primary" variant="tonal" :disabled="!pendingPoolModelId" @click="addPoolModel">添加</v-btn>
          </v-card-actions>
        </v-card>
      </v-dialog>
    </template>
  </div>
</template>

<style scoped>
.empty-tip {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 80px 0;
  color: rgba(var(--v-theme-on-surface), 0.45);
  font-size: 14px;
}

.pool-block {
  border: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  border-radius: 10px;
  padding: 8px 10px;
  background: rgba(var(--v-theme-on-surface), 0.015);
}

.pool-order {
  width: 22px;
  height: 22px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  background: rgba(var(--v-theme-primary), 0.12);
  color: rgb(var(--v-theme-primary));
  font-size: 12px;
  font-weight: 600;
  margin-right: 10px;
}
</style>
