<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import http, { errorMessage } from '@/api/http'
import { useAgentConfigStore } from '@/stores/agentConfig'
import { useBotsStore } from '@/stores/bots'
import { useToolsStore } from '@/stores/tools'
import { useNotifyStore } from '@/stores/notify'

interface ModuleTool {
  name: string
  description: string
  parameters: Record<string, any>
  category: string
  source: string
  module: string
  module_label: string
  enabled: boolean
  ready: boolean
  effective: boolean
  prerequisite: string
}

const agent = useAgentConfigStore()
const bots = useBotsStore()
const toolsStore = useToolsStore()
const notify = useNotifyStore()

const tools = ref<ModuleTool[]>([])
const loading = ref(false)

const enabledMap = computed<Record<string, boolean>>(() => agent.draft.module_tools_enabled || {})

const logEnabled = computed<boolean>({
  get: () => !!agent.draft.module_tools_log_enabled,
  set: (v: boolean) => agent.onChange('module_tools_log_enabled', !!v),
})

const groups = computed(() => {
  const map = new Map<string, ModuleTool[]>()
  for (const tool of tools.value) {
    const category = tool.module_label || tool.category || '模块工具'
    if (!map.has(category)) map.set(category, [])
    map.get(category)!.push(tool)
  }
  return Array.from(map.entries()).map(([category, items]) => ({ category, items }))
})

function toggleTool(tool: ModuleTool) {
  const next = { ...enabledMap.value, [tool.name]: !tool.enabled }
  agent.onChange('module_tools_enabled', next)
  tool.enabled = !tool.enabled
  tool.effective = tool.enabled && tool.ready
}

async function loadTools() {
  if (!agent.botId) return
  loading.value = true
  try {
    const res = await http.get<{ ok: boolean; log_enabled: boolean; tools: ModuleTool[] }>('/api/agent/module/tools', {
      params: { bot_id: agent.botId },
    })
    tools.value = res.data.tools || []
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    loading.value = false
  }
}

function restoreScroll() {
  const top = toolsStore.scrollTop.module || 0
  if (top) window.scrollTo(0, top)
}

function saveScroll() {
  toolsStore.setScroll('module', window.scrollY || 0)
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
  () => bots.currentBot?.bot_id,
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
        <v-icon icon="mdi-puzzle-outline" class="mr-2" color="secondary" /> 模块 Tools
        <v-spacer />
        <v-switch
          v-model="logEnabled"
          label="输出工具调用日志"
          color="primary"
          density="compact"
          hide-details
        />
      </v-card-title>
      <v-card-text class="text-caption" style="opacity: 0.65">
        展示各功能模块通过 @tool 暴露给 LLM 的工具。模块本身的启停仍在 功能模块 页面管理。
      </v-card-text>
    </v-card>

    <v-card variant="outlined" class="mb-4">
      <v-card-title class="d-flex align-center">
        <v-icon icon="mdi-puzzle-outline" class="mr-2" color="secondary" /> 模块工具
        <v-spacer />
        <v-chip v-if="tools.length" size="small">{{ tools.length }} 个</v-chip>
      </v-card-title>
      <v-card-text>
        <v-progress-linear v-if="loading" indeterminate color="primary" />

        <div class="tool-list">
          <template v-for="group in groups" :key="group.category">
            <div class="tool-category">
              <span class="tool-category-name">{{ group.category }}</span>
              <span class="tool-category-count">{{ group.items.length }}</span>
            </div>
            <div
              v-for="tool in group.items"
              :key="tool.name"
              class="tool-item"
              :class="{ 'is-off': !tool.effective }"
            >
              <div class="tool-row">
                <div class="tool-info">
                  <div class="tool-name">{{ tool.name }}</div>
                  <div class="tool-desc">{{ tool.description }}</div>
                  <div class="tool-meta">
                    <v-chip size="small" variant="tonal" color="secondary">{{ tool.module }}</v-chip>
                    <v-chip v-if="!tool.ready && tool.prerequisite" size="small" variant="tonal" color="warning">
                      {{ tool.prerequisite }}
                    </v-chip>
                  </div>
                </div>
                <v-switch
                  :model-value="tool.enabled"
                  :disabled="!tool.ready"
                  color="primary"
                  density="compact"
                  hide-details
                  @update:model-value="() => toggleTool(tool)"
                />
              </div>
            </div>
          </template>
          <div v-if="!tools.length && !loading" class="text-caption text-center pa-4" style="opacity: 0.55">
            暂无模块工具（模块尚未通过 @tool 暴露工具）
          </div>
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
}

.tool-category:first-child {
  margin-top: 0;
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
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 4px;
}
</style>
