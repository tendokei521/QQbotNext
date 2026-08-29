<script setup lang="ts">
import { useAgentConfigStore } from '@/stores/agentConfig'

withDefaults(
  defineProps<{
    title: string
    subtitle?: string
    icon?: string
    color?: string
    backLabel?: string
  }>(),
  {
    subtitle: '',
    icon: 'mdi-cogs',
    color: 'primary',
    backLabel: '返回 Agent 面板',
  },
)

const agent = useAgentConfigStore()
</script>

<template>
  <div>
    <div class="app-page-header" style="align-items: center">
      <div class="d-flex align-center gap-2 flex-wrap">
        <v-btn size="small" variant="text" prepend-icon="mdi-arrow-left" class="mr-1" @click="$router.push('/agent')">
          {{ backLabel }}
        </v-btn>
        <h1 class="app-page-title">
          <v-icon :icon="icon" size="28" class="mr-1" :color="color" />
          {{ title }}
        </h1>
        <v-chip v-if="agent.saveStatus === 'saving'" size="small" color="primary" variant="flat">
          <v-progress-circular size="12" indeterminate class="mr-1" /> 保存中…
        </v-chip>
        <v-chip v-else-if="agent.saveStatus === 'error'" size="small" color="error" variant="flat">保存失败</v-chip>
        <v-chip v-else-if="agent.saveStatus === 'dirty'" size="small" color="warning" variant="flat">未保存</v-chip>
      </div>
      <v-btn size="small" color="primary" variant="tonal" prepend-icon="mdi-content-save" :loading="agent.saveStatus === 'saving'" @click="agent.save()">
        保存配置
      </v-btn>
    </div>
    <div v-if="subtitle" class="app-page-subtitle">{{ subtitle }}</div>

    <v-progress-linear v-if="agent.loading" indeterminate color="primary" class="mb-3" />

    <slot />
  </div>
</template>
