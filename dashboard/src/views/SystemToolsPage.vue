<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import http, { errorMessage } from '@/api/http'
import { useAgentConfigStore } from '@/stores/agentConfig'
import { useBotsStore } from '@/stores/bots'
import { useToolsStore } from '@/stores/tools'
import { useNotifyStore } from '@/stores/notify'

interface SystemTool {
  name: string
  description: string
  parameters: Record<string, any>
  category: string
  source: string
  enabled: boolean
  ready: boolean
  effective: boolean
  prerequisite: string
}

const agent = useAgentConfigStore()
const bots = useBotsStore()
const toolsStore = useToolsStore()
const notify = useNotifyStore()

const tools = ref<SystemTool[]>([])
const loading = ref(false)

const enabledMap = computed<Record<string, boolean>>(() => agent.draft.system_tools_enabled || {})

const logEnabled = computed<boolean>({
  get: () => !!agent.draft.system_tools_log_enabled,
  set: (v: boolean) => agent.onChange('system_tools_log_enabled', !!v),
})

function isCollapsed(category: string): boolean {
  return !!toolsStore.collapsedOf('system')[category]
}

function toggleCategory(category: string) {
  const next = { ...toolsStore.collapsedOf('system'), [category]: !isCollapsed(category) }
  toolsStore.setCollapsed('system', next)
}

const groups = computed(() => {
  const map = new Map<string, SystemTool[]>()
  for (const tool of tools.value) {
    const category = tool.category || '系统工具'
    if (!map.has(category)) map.set(category, [])
    map.get(category)!.push(tool)
  }
  return Array.from(map.entries()).map(([category, items]) => ({ category, items }))
})

function toggleTool(tool: SystemTool) {
  const next = { ...enabledMap.value, [tool.name]: !tool.enabled }
  agent.onChange('system_tools_enabled', next)
  tool.enabled = !tool.enabled
  tool.effective = tool.enabled && tool.ready
}

async function loadTools() {
  if (!agent.botId) return
  loading.value = true
  try {
    const res = await http.get<{ ok: boolean; log_enabled: boolean; tools: SystemTool[] }>('/api/agent/system/tools', {
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
  const top = toolsStore.scrollTop.system || 0
  if (top) window.scrollTo(0, top)
}

function saveScroll() {
  toolsStore.setScroll('system', window.scrollY || 0)
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
        <v-icon icon="mdi-cog-outline" class="mr-2" color="primary" /> System Tools
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
        控制框架内置系统工具的注入；关闭后该工具不再出现在 LLM 的 function calling 列表中。日志开关只影响普通调用日志，异常/超时仍会保留。
      </v-card-text>
    </v-card>

    <v-card variant="outlined" class="mb-4">
      <v-card-title class="d-flex align-center">
        <v-icon icon="mdi-tools" class="mr-2" color="primary" /> 系统工具
        <v-spacer />
        <v-chip v-if="tools.length" size="small">{{ tools.length }} 个</v-chip>
      </v-card-title>
      <v-card-text>
        <v-progress-linear v-if="loading" indeterminate color="primary" />

        <div class="tool-list">
          <template v-for="group in groups" :key="group.category">
            <div class="tool-category" @click="toggleCategory(group.category)">
              <v-icon :icon="isCollapsed(group.category) ? 'mdi-chevron-down' : 'mdi-chevron-up'" size="small" />
              <span class="tool-category-name">{{ group.category }}</span>
              <span class="tool-category-count">{{ group.items.length }}</span>
            </div>
            <template v-if="!isCollapsed(group.category)">
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
                    <div v-if="!tool.ready && tool.prerequisite" class="tool-prereq">
                      <v-icon size="x-small" icon="mdi-alert-circle-outline" color="warning" />
                      {{ tool.prerequisite }}
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
          </template>
          <div v-if="!tools.length && !loading" class="text-caption text-center pa-4" style="opacity: 0.55">
            暂无系统工具
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
  cursor: pointer;
  user-select: none;
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

.tool-prereq {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: rgb(var(--v-theme-warning));
}
</style>
