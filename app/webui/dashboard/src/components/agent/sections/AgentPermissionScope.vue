<script setup lang="ts">
/**
 * 「权限」分区的定制 UI：黑白名单编辑器。
 * 包一层是为了让它和其它分区定制组件一样无 props、自己读 store，
 * 从而能被 AgentSectionPage 用统一的 <component :is> 挂载。
 */
import PermissionEditor from '@/components/config/PermissionEditor.vue'
import { useAgentConfigStore } from '@/stores/agentConfig'

const agent = useAgentConfigStore()
</script>

<template>
  <v-card variant="outlined" class="mb-4">
    <v-card-title class="d-flex align-center">
      <v-icon icon="mdi-shield-account-outline" class="mr-2" color="success" /> 响应范围控制
    </v-card-title>
    <v-card-text>
      <PermissionEditor :model-value="agent.permission" @update:model-value="agent.onPermissionChange" />
    </v-card-text>
  </v-card>
</template>
