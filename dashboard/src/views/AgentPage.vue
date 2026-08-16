<script setup lang="ts">
import { onMounted, reactive, ref, watch } from 'vue'
import http, { errorMessage } from '@/api/http'
import { useBotsStore } from '@/stores/bots'
import { useNotifyStore } from '@/stores/notify'
import type { PermissionConfig } from '@/stores/modules'
import ConfigForm from '@/components/config/ConfigForm.vue'
import PermissionEditor from '@/components/config/PermissionEditor.vue'
import AgentPanels from '@/components/agent/AgentPanels.vue'

const bots = useBotsStore()
const notify = useNotifyStore()

const botId = ref<number | null>(null)
const enabled = ref(false)
const schema = ref<Record<string, any>>({})
const draft = reactive<Record<string, any>>({})
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

async function load() {
  botId.value = bots.currentBot?.bot_id ?? null
  if (botId.value === null) {
    notify.push('请先连接 Bot 后配置 Agent', 'warning')
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
    }>('/api/agent/config', { params: { bot_id: botId.value } })
    const data = res.data
    enabled.value = !!data.enabled
    schema.value = data.schema || {}
    Object.keys(draft).forEach((k) => delete draft[k])
    Object.assign(draft, data.config || {})
    permission.value = {
      group_mode: data.permission?.group_mode || 'blacklist',
      group_list: [...(data.permission?.group_list || [])],
      user_mode: data.permission?.user_mode || 'blacklist',
      user_list: [...(data.permission?.user_list || [])],
    }
    saveStatus.value = 'clean'
    dirtyFlag = false
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    loading.value = false
  }
}

function onChange(key: string, value: any) {
  draft[key] = value
  dirtyFlag = true
  saveStatus.value = 'dirty'
  if (autosaveTimer) clearTimeout(autosaveTimer)
  autosaveTimer = window.setTimeout(doSave, 2000)
}

function onPermissionChange(v: PermissionConfig) {
  permission.value = v
  dirtyFlag = true
  saveStatus.value = 'dirty'
  if (autosaveTimer) clearTimeout(autosaveTimer)
  autosaveTimer = window.setTimeout(doSave, 2000)
}

function onEnabledChange(v: boolean | null) {
  enabled.value = !!v
  dirtyFlag = true
  saveStatus.value = 'dirty'
  if (autosaveTimer) clearTimeout(autosaveTimer)
  autosaveTimer = window.setTimeout(doSave, 2000)
}

async function doSave() {
  if (autosaveTimer) {
    clearTimeout(autosaveTimer)
    autosaveTimer = null
  }
  if (!dirtyFlag) return
  saveStatus.value = 'saving'
  try {
    await http.post(
      '/api/agent/config',
      { config: { ...draft }, permission: permission.value, enabled: enabled.value },
      { params: { bot_id: botId.value } },
    )
    dirtyFlag = false
    saveStatus.value = 'clean'
    notify.push('Agent 配置已保存', 'success')
  } catch (err) {
    saveStatus.value = 'error'
    notify.push(errorMessage(err), 'error')
  }
}

watch(
  () => bots.currentBot?.bot_id,
  () => load(),
)

onMounted(load)
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

      <v-card variant="outlined" class="mb-4">
        <v-card-title class="d-flex align-center">
          <v-icon icon="mdi-shield-account-outline" class="mr-2" color="primary" /> 响应范围控制
        </v-card-title>
        <v-card-text>
          <PermissionEditor :model-value="permission" @update:model-value="onPermissionChange" />
        </v-card-text>
      </v-card>

      <v-card variant="outlined" class="mb-4">
        <v-card-title class="d-flex align-center">
          <v-icon icon="mdi-cogs" class="mr-2" color="primary" /> Agent 配置
          <v-spacer />
          <v-btn size="small" color="primary" variant="tonal" prepend-icon="mdi-content-save" :loading="saveStatus === 'saving'" @click="doSave">
            保存配置
          </v-btn>
        </v-card-title>
        <v-card-text>
          <ConfigForm :module-name="'agent'" :schema="schema" :config="draft" :bot-id="botId" @change="onChange" />
        </v-card-text>
      </v-card>

      <AgentPanels :bot-id="botId" />
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
</style>
