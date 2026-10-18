<script setup lang="ts">
/**
 * 「定时任务 / 主动消息」分区的定制 UI：任务与主动会话面板。
 * 包一层是为了统一成无 props、自读 store 的形态。
 */
import AgentPanels from '@/components/agent/AgentPanels.vue'
import { useAgentConfigStore } from '@/stores/agentConfig'

const agent = useAgentConfigStore()
</script>

<template>
  <AgentPanels :bot-id="agent.botId" />
</template>
