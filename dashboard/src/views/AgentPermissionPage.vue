<script setup lang="ts">
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
