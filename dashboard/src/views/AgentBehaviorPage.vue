<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import ConfigForm from '@/components/config/ConfigForm.vue'
import AgentSubPage from '@/components/agent/AgentSubPage.vue'
import { useAgentConfigStore } from '@/stores/agentConfig'
import { useBotsStore } from '@/stores/bots'
import { filterSchemaByPage } from '@/utils/schema'

const agent = useAgentConfigStore()
const bots = useBotsStore()
const schema = computed(() => filterSchemaByPage(agent.schema, 'behavior'))

onMounted(() => agent.load())
watch(
  () => bots.currentBot?.bot_id,
  () => agent.load(true),
)
</script>

<template>
  <AgentSubPage title="对话行为" subtitle="用户信息感知、回复打断、触发与冷却" icon="mdi-account-voice" color="secondary">
    <v-card variant="outlined" class="mb-4">
      <v-card-title class="d-flex align-center">
        <v-icon icon="mdi-account-voice" class="mr-2" color="secondary" /> 对话行为
      </v-card-title>
      <v-card-text>
        <ConfigForm :module-name="'agent'" :schema="schema" :config="agent.draft" :bot-id="agent.botId" @change="agent.onChange" />
      </v-card-text>
    </v-card>
  </AgentSubPage>
</template>
