<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import ConfigForm from '@/components/config/ConfigForm.vue'
import AgentSubPage from '@/components/agent/AgentSubPage.vue'
import { useAgentConfigStore } from '@/stores/agentConfig'
import { useBotsStore } from '@/stores/bots'
import { filterSchemaByPage } from '@/utils/schema'

const agent = useAgentConfigStore()
const bots = useBotsStore()
const schema = computed(() => filterSchemaByPage(agent.schema, 'mcp'))

const servers = computed<any[]>(() => {
  const raw = agent.draft.mcp_servers
  if (Array.isArray(raw)) return raw
  if (typeof raw === 'string' && raw.trim()) {
    try {
      return JSON.parse(raw)
    } catch {
      return []
    }
  }
  return []
})

onMounted(() => agent.load())
watch(
  () => bots.currentBot?.bot_id,
  () => agent.load(true),
)
</script>

<template>
  <AgentSubPage title="MCP 工具" subtitle="MCP stdio server 配置" icon="mdi-server-network" color="blue-grey">
    <v-card variant="outlined" class="mb-4">
      <v-card-title class="d-flex align-center">
        <v-icon icon="mdi-server-network" class="mr-2" color="blue-grey" /> MCP Servers
        <v-spacer />
        <v-chip v-if="servers.length" color="primary" size="small">{{ servers.length }} 个 Server</v-chip>
        <v-chip v-else color="grey" size="small">未配置</v-chip>
      </v-card-title>
      <v-card-text>
        <v-list v-if="servers.length" density="compact">
          <v-list-item v-for="(s, i) in servers" :key="i">
            <v-list-item-title>{{ s.name || `Server ${i + 1}` }}</v-list-item-title>
            <v-list-item-subtitle>{{ s.command }} {{ (s.args || []).join(' ') }}</v-list-item-subtitle>
          </v-list-item>
        </v-list>
        <div v-else class="text-caption text-center pa-3" style="opacity: 0.55">
          暂无 MCP Server，请在下方 JSON 配置中添加
        </div>
      </v-card-text>
    </v-card>

    <v-card variant="outlined" class="mb-4">
      <v-card-title class="d-flex align-center">
        <v-icon icon="mdi-json" class="mr-2" color="blue-grey" /> JSON 配置
      </v-card-title>
      <v-card-text>
        <ConfigForm :module-name="'agent'" :schema="schema" :config="agent.draft" :bot-id="agent.botId" @change="agent.onChange" />
      </v-card-text>
    </v-card>
  </AgentSubPage>
</template>
