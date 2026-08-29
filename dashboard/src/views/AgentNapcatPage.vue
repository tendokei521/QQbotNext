<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import http, { errorMessage } from '@/api/http'
import ConfigForm from '@/components/config/ConfigForm.vue'
import AgentSubPage from '@/components/agent/AgentSubPage.vue'
import { useAgentConfigStore } from '@/stores/agentConfig'
import { useBotsStore } from '@/stores/bots'
import { useNotifyStore } from '@/stores/notify'
import { filterSchemaByPage } from '@/utils/schema'

interface NapCatTool {
  name: string
  description: string
  parameters: Record<string, any>
  risk: 'read' | 'send' | 'admin'
  permission: string
  scopes: string[]
  enabled: boolean
}

const agent = useAgentConfigStore()
const bots = useBotsStore()
const notify = useNotifyStore()
const schema = computed(() => filterSchemaByPage(agent.schema, 'napcat'))

const tools = ref<NapCatTool[]>([])
const loading = ref(false)
const detailKey = ref('')

const enabled = computed<boolean>({
  get: () => !!agent.draft.napcat_tools_enable,
  set: (v: boolean) => agent.onChange('napcat_tools_enable', v),
})

const denied = computed<string[]>(() => Array.isArray(agent.draft.napcat_tools_denied) ? agent.draft.napcat_tools_denied : [])
const allowed = computed<string[]>(() => Array.isArray(agent.draft.napcat_tools_allowed) ? agent.draft.napcat_tools_allowed : [])

function isToolOn(name: string): boolean {
  if (denied.value.includes(name)) return false
  if (allowed.value.length && !allowed.value.includes(name)) return false
  return true
}

function toggleTool(name: string) {
  if (isToolOn(name)) {
    agent.onChange('napcat_tools_denied', Array.from(new Set([...denied.value, name])))
    agent.onChange('napcat_tools_allowed', allowed.value.filter((x) => x !== name))
  } else {
    agent.onChange('napcat_tools_denied', denied.value.filter((x) => x !== name))
    if (allowed.value.length) {
      agent.onChange('napcat_tools_allowed', Array.from(new Set([...allowed.value, name])))
    }
  }
}

function resetToggles() {
  agent.onChange('napcat_tools_denied', [])
  agent.onChange('napcat_tools_allowed', [])
}

function riskColor(risk: string): string {
  return risk === 'read' ? 'success' : risk === 'send' ? 'info' : 'warning'
}

function permissionLabel(permission: string): string {
  return permission === 'group_admin' ? '群管理' : permission === 'group_owner' ? '群主' : permission === 'owner' ? 'Bot拥有者' : '成员'
}

function riskLabel(risk: string): string {
  return risk === 'read' ? '只读' : risk === 'send' ? '消息' : '管理'
}

async function loadTools() {
  if (!agent.botId) return
  loading.value = true
  try {
    const res = await http.get<{ ok: boolean; tools: NapCatTool[] }>('/api/agent/napcat/tools', {
      params: { bot_id: agent.botId },
    })
    tools.value = res.data.tools || []
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await agent.load()
  loadTools()
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
  <AgentSubPage title="Napcat Tools" subtitle="把 NapCat/OneBot API 暴露给 LLM 作为 function calling 工具" icon="mdi-robot-industrial" color="teal">
    <v-card variant="outlined" class="mb-4">
      <v-card-title class="d-flex align-center">
        <v-icon icon="mdi-power" class="mr-2" color="teal" /> 总开关
        <v-spacer />
        <v-switch v-model="enabled" color="primary" density="compact" hide-details />
      </v-card-title>
      <v-card-text class="text-caption" style="opacity: 0.65">
        开启后，LLM 将获得调用 NapCat/OneBot API 的能力。请谨慎开启敏感管理工具。
        <div class="mt-2">
          <v-switch
            :model-value="!!agent.draft.napcat_tools_debug"
            label="NapCat 调试日志（完整记录请求/响应）"
            color="warning"
            density="compact"
            hide-details
            @update:model-value="(v: any) => agent.onChange('napcat_tools_debug', !!v)"
          />
        </div>
      </v-card-text>
    </v-card>

    <v-card variant="outlined" class="mb-4">
      <v-card-title class="d-flex align-center">
        <v-icon icon="mdi-robot-industrial" class="mr-2" color="teal" /> 可用工具
        <v-spacer />
        <v-btn size="small" variant="tonal" :disabled="!enabled" @click="resetToggles">恢复默认</v-btn>
      </v-card-title>
      <v-card-text>
        <v-progress-linear v-if="loading" indeterminate color="primary" />

        <div class="tool-list">
          <div v-for="tool in tools" :key="tool.name" class="tool-item" :class="{ 'is-off': !enabled || !isToolOn(tool.name) }">
            <div class="tool-row">
              <div class="tool-info">
                <div class="tool-name">{{ tool.name }}</div>
                <div class="tool-desc">{{ tool.description }}</div>
                <div class="tool-meta d-flex gap-2 flex-wrap">
                  <v-chip size="x-small" variant="tonal" :color="riskColor(tool.risk)">{{ riskLabel(tool.risk) }}</v-chip>
                  <v-chip size="x-small" variant="tonal">权限: {{ permissionLabel(tool.permission) }}</v-chip>
                  <v-chip size="x-small" variant="tonal">作用域: {{ tool.scopes.join(', ') }}</v-chip>
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
            <div v-if="detailKey === tool.name" class="tool-detail">
              <div class="text-caption mb-1">参数 Schema</div>
              <pre class="tool-pre">{{ JSON.stringify(tool.parameters, null, 2) }}</pre>
            </div>
            <v-btn v-else size="x-small" variant="text" prepend-icon="mdi-code-json" @click="detailKey = tool.name">
              查看参数
            </v-btn>
          </div>
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
  </AgentSubPage>
</template>

<style scoped>
.tool-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
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
  font-size: 15px;
}

.tool-desc {
  font-size: 13px;
  color: rgba(var(--v-theme-on-surface), 0.65);
  margin: 2px 0 4px;
}

.tool-meta {
  align-items: center;
}

.tool-detail {
  margin-top: 10px;
  border-top: 1px dashed rgba(var(--v-theme-on-surface), 0.12);
  padding-top: 10px;
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
