<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import ConfigForm from '@/components/config/ConfigForm.vue'
import AgentSubPage from '@/components/agent/AgentSubPage.vue'
import AgentPanels from '@/components/agent/AgentPanels.vue'
import { useAgentConfigStore } from '@/stores/agentConfig'
import { useBotsStore } from '@/stores/bots'
import { filterSchemaByPage } from '@/utils/schema'

const agent = useAgentConfigStore()
const bots = useBotsStore()
const schema = computed(() => filterSchemaByPage(agent.schema, 'panels'))

onMounted(() => agent.load())
watch(
  () => bots.currentBot?.bot_id,
  () => agent.load(true),
)
</script>

<template>
  <AgentSubPage title="定时任务 / 主动消息" subtitle="定时触发与主动发言" icon="mdi-clock-outline" color="brown">
    <v-card v-if="agent.botId !== null" variant="outlined" class="mb-4">
      <v-card-title class="d-flex align-center">
        <v-icon icon="mdi-cog-outline" class="mr-2" color="primary" /> 主动消息 / 定时任务配置
      </v-card-title>
      <v-card-text>
        <ConfigForm
          :module-name="'agent'"
          :schema="schema"
          :config="agent.draft"
          :bot-id="agent.botId"
          @change="agent.onChange"
        />
      </v-card-text>
    </v-card>

    <AgentPanels :bot-id="agent.botId" />
  </AgentSubPage>
</template>
