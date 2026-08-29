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
        <v-chip v-if="agent.draft.mcp_servers" color="primary" size="small">已配置</v-chip>
        <v-chip v-else color="grey" size="small">未配置</v-chip>
      </v-card-title>
      <v-card-text>
        <ConfigForm :module-name="'agent'" :schema="schema" :config="agent.draft" :bot-id="agent.botId" @change="agent.onChange" />
      </v-card-text>
    </v-card>
  </AgentSubPage>
</template>
