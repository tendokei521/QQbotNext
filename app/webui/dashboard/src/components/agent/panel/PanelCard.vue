<script setup lang="ts">
/**
 * AI 对话面板里的通用卡片：标题 + 可选说明 + 内容 + 右上角操作区。
 *
 * 面板卡片统一走这一个壳，避免每张卡各写一套 v-card/标题/间距。
 */
withDefaults(
  defineProps<{
    title: string
    icon?: string
    color?: string
    subtitle?: string
  }>(),
  { icon: 'mdi-card-outline', color: 'primary', subtitle: '' },
)
</script>

<template>
  <v-card variant="outlined" class="mb-4">
    <v-card-title class="d-flex align-center">
      <v-icon :icon="icon" :color="color" class="mr-2" />
      {{ title }}
      <v-spacer />
      <slot name="actions" />
    </v-card-title>
    <v-card-text v-if="subtitle" class="text-caption pb-0 pt-0" style="opacity: 0.72">
      {{ subtitle }}
    </v-card-text>
    <v-card-text>
      <slot />
    </v-card-text>
  </v-card>
</template>
