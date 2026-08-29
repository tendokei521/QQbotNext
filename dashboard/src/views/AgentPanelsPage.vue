<script setup lang="ts">
import { onMounted, watch } from 'vue'
import AgentSubPage from '@/components/agent/AgentSubPage.vue'
import AgentPanels from '@/components/agent/AgentPanels.vue'
import { useAgentConfigStore } from '@/stores/agentConfig'
import { useBotsStore } from '@/stores/bots'

const agent = useAgentConfigStore()
const bots = useBotsStore()

onMounted(() => agent.load())
watch(
  () => bots.currentBot?.bot_id,
  () => agent.load(true),
)
</script>

<template>
  <AgentSubPage title="定时任务 / 主动消息" subtitle="定时触发与主动发言" icon="mdi-clock-outline" color="brown">
    <AgentPanels :bot-id="agent.botId" />
  </AgentSubPage>
</template>
