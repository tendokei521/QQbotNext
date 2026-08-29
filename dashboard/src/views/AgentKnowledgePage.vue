<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import ConfigForm from '@/components/config/ConfigForm.vue'
import AgentSubPage from '@/components/agent/AgentSubPage.vue'
import { useAgentConfigStore } from '@/stores/agentConfig'
import { useBotsStore } from '@/stores/bots'
import { filterSchemaByPage } from '@/utils/schema'

const agent = useAgentConfigStore()
const bots = useBotsStore()
const schema = computed(() => filterSchemaByPage(agent.schema, 'knowledge'))

onMounted(() => agent.load())
watch(
  () => bots.currentBot?.bot_id,
  () => agent.load(true),
)
</script>

<template>
  <AgentSubPage title="知识库" subtitle="知识库检索与 Embedding 模型" icon="mdi-book-open-variant" color="deep-purple">
    <v-card variant="outlined" class="mb-4">
      <v-card-title class="d-flex align-center">
        <v-icon icon="mdi-book-open-variant" class="mr-2" color="deep-purple" /> 知识库配置
        <v-spacer />
        <v-chip v-if="agent.draft.knowledge_enable" color="success" size="small">启用</v-chip>
        <v-chip v-else color="grey" size="small">未启用</v-chip>
      </v-card-title>
      <v-card-text>
        <ConfigForm :module-name="'agent'" :schema="schema" :config="agent.draft" :bot-id="agent.botId" @change="agent.onChange" />
      </v-card-text>
    </v-card>
  </AgentSubPage>
</template>
