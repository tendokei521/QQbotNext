<script setup lang="ts">
import { onMounted, watch } from 'vue'
import AgentSubPage from '@/components/agent/AgentSubPage.vue'
import PermissionEditor from '@/components/config/PermissionEditor.vue'
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
  <AgentSubPage title="权限" subtitle="黑白名单与角色权限" icon="mdi-shield-account-outline" color="success">
    <v-card variant="outlined" class="mb-4">
      <v-card-title class="d-flex align-center">
        <v-icon icon="mdi-shield-account-outline" class="mr-2" color="success" /> 响应范围控制
      </v-card-title>
      <v-card-text>
        <PermissionEditor :model-value="agent.permission" @update:model-value="agent.onPermissionChange" />
      </v-card-text>
    </v-card>
  </AgentSubPage>
</template>
