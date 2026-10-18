<script setup lang="ts">
import { computed } from 'vue'
import ConfigForm from '@/components/config/ConfigForm.vue'
import AgentPanels from '@/components/agent/AgentPanels.vue'
import { useAgentConfigStore } from '@/stores/agentConfig'
import { filterSchemaByPage } from '@/utils/schema'

const agent = useAgentConfigStore()
const schema = computed(() => filterSchemaByPage(agent.schema, 'panels'))
</script>

<template>
  <v-card variant="outlined" class="mb-4">
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
</template>
