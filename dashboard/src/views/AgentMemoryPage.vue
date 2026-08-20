<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import http, { errorMessage } from '@/api/http'
import { useBotsStore } from '@/stores/bots'
import { useNotifyStore } from '@/stores/notify'
import ConfigForm from '@/components/config/ConfigForm.vue'
import { filterSchemaByGroup } from '@/utils/schema'

const bots = useBotsStore()
const notify = useNotifyStore()

const MEMORY_GROUP = 'group_memory'

const botId = ref<number | null>(null)
const schema = ref<Record<string, any>>({})
const draft = reactive<Record<string, any>>({})
const loading = ref(false)
const saveStatus = ref<'clean' | 'dirty' | 'saving' | 'error'>('clean')
let autosaveTimer: number | null = null
let dirtyFlag = false
let pendingBotId: number | null = null
let editSeq = 0

async function load() {
  if (autosaveTimer) {
    clearTimeout(autosaveTimer)
    autosaveTimer = null
  }
  if (!bots.bots.length) {
    try {
      await bots.fetchBots()
    } catch {
      /* 忽略列表加载失败 */
    }
  }
  if (bots.currentIndex === null && bots.bots.length) {
    bots.restoreSelection()
  }
  botId.value = bots.currentBot?.bot_id ?? null
  if (botId.value === null) return

  loading.value = true
  try {
    const res = await http.get<{
      ok: boolean
      bot_id: number | null
      config: Record<string, any>
      schema: Record<string, any>
    }>('/api/agent/config', { params: { bot_id: botId.value } })
    const data = res.data
    schema.value = filterSchemaByGroup(data.schema || {}, MEMORY_GROUP)
    Object.keys(draft).forEach((k) => delete draft[k])
    Object.assign(draft, data.config || {})
    saveStatus.value = 'clean'
    dirtyFlag = false
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    loading.value = false
  }
}

function scheduleSave() {
  pendingBotId = botId.value
  dirtyFlag = true
  editSeq += 1
  saveStatus.value = 'dirty'
  if (autosaveTimer) clearTimeout(autosaveTimer)
  autosaveTimer = window.setTimeout(doSave, 2000)
}

function onChange(key: string, value: any) {
  draft[key] = value
  scheduleSave()
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
      { config: { ...draft } },
      { params: { bot_id: targetBotId } },
    )
    if (targetBotId !== botId.value) return
    if (editSeq !== snapshotSeq) return
    dirtyFlag = false
    saveStatus.value = 'clean'
    notify.push('Agent 长期记忆配置已保存', 'success')
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

const hasItems = computed(() => Object.keys(schema.value).length > 0)
</script>

<template>
  <div>
    <div class="app-page-header" style="align-items: center">
      <div class="d-flex align-center gap-2 flex-wrap">
        <h1 class="app-page-title">Agent 长期记忆</h1>
        <v-chip size="small" variant="tonal" color="warning">实验性</v-chip>
        <v-chip v-if="saveStatus === 'saving'" size="small" color="primary" variant="flat">
          <v-progress-circular size="12" indeterminate class="mr-1" /> 保存中…
        </v-chip>
        <v-chip v-else-if="saveStatus === 'error'" size="small" color="error" variant="flat">保存失败</v-chip>
        <v-chip v-else-if="saveStatus === 'dirty'" size="small" color="warning" variant="flat">未保存</v-chip>
      </div>
      <div class="d-flex align-center gap-3">
        <v-btn size="small" color="primary" variant="tonal" prepend-icon="mdi-content-save" :loading="saveStatus === 'saving'" @click="doSave">
          保存配置
        </v-btn>
      </div>
    </div>
    <div class="app-page-subtitle">长期记忆与感知增强提示词细调（实验性）</div>

    <div v-if="botId === null" class="empty-tip">
      <v-icon icon="mdi-brain" size="56" color="rgba(var(--v-theme-on-surface), 0.3)" />
      <div>请先在顶栏选择并连接一个 Bot，再配置 Agent 长期记忆</div>
      <v-btn variant="tonal" prepend-icon="mdi-refresh" class="mt-2" @click="load">重试</v-btn>
    </div>

    <template v-else>
      <v-progress-linear v-if="loading" indeterminate color="primary" />

      <v-card variant="outlined" class="mb-4">
        <v-card-title class="d-flex align-center">
          <v-icon icon="mdi-brain" class="mr-2" color="warning" /> 长期记忆（实验性）
          <v-spacer />
          <v-btn size="small" variant="tonal" prepend-icon="mdi-arrow-left" @click="$router.push('/agent')">
            返回 Agent 面板
          </v-btn>
        </v-card-title>
        <v-card-text>
          <ConfigForm :module-name="'agent'" :schema="schema" :config="draft" :bot-id="botId" @change="onChange" />
          <div v-if="!hasItems && !loading" class="text-caption mt-2" style="color: rgba(var(--v-theme-on-surface), 0.45)">
            当前没有可用的长期记忆配置项。
          </div>
        </v-card-text>
      </v-card>
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
