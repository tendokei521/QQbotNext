<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import ConfigForm from '@/components/config/ConfigForm.vue'
import AgentSubPage from '@/components/agent/AgentSubPage.vue'
import { useAgentConfigStore } from '@/stores/agentConfig'
import { useBotsStore } from '@/stores/bots'
import { filterSchemaByPage } from '@/utils/schema'

const agent = useAgentConfigStore()
const bots = useBotsStore()

const schema = computed(() => filterSchemaByPage(agent.schema, 'basic'))

onMounted(() => agent.load())
watch(
  () => bots.currentBot?.bot_id,
  () => agent.load(true),
)
</script>

<template>
  <AgentSubPage title="基础配置" subtitle="提示词、群/私聊开关、历史与触发" icon="mdi-tune-variant" color="primary">
    <v-card v-if="agent.botId !== null" variant="outlined" class="mb-4">
      <v-card-title class="d-flex align-center">
        <v-icon icon="mdi-tune-variant" class="mr-2" color="primary" /> 基础配置
      </v-card-title>
      <v-card-text>
        <ConfigForm :module-name="'agent'" :schema="schema" :config="agent.draft" :bot-id="agent.botId" @change="agent.onChange" />
      </v-card-text>
    </v-card>
    <div v-else class="empty-tip">
      <v-icon icon="mdi-robot-off-outline" size="56" color="rgba(var(--v-theme-on-surface), 0.3)" />
      <div>请先在顶栏选择并连接一个 Bot</div>
    </div>
  </AgentSubPage>
</template>

<style scoped>
.empty-tip {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 80px 0;
  color: rgba(var(--v-theme-on-surface), 0.45);
}
</style>
