<script setup lang="ts">
withDefaults(
  defineProps<{
    icon?: string
    title?: string
    description?: string
  }>(),
  { icon: 'mdi-inbox-outline', title: '暂无数据', description: '' },
)
</script>

<template>
  <div class="app-empty">
    <div class="empty-icon">
      <v-icon :icon="icon" size="34" />
    </div>
    <div v-if="title" class="empty-title">{{ title }}</div>
    <div v-if="description" class="empty-desc">{{ description }}</div>
    <div v-if="$slots.default" class="empty-action">
      <slot />
    </div>
  </div>
</template>

<style scoped>
.app-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 48px 16px;
  text-align: center;
}

.empty-icon {
  width: 64px;
  height: 64px;
  border-radius: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 6px;
  color: rgba(var(--v-theme-on-surface), 0.38);
  background: rgba(var(--v-theme-on-surface), 0.045);
}

.empty-title {
  font-size: 14px;
  font-weight: 600;
  color: rgba(var(--v-theme-on-surface), 0.62);
}

.empty-desc {
  font-size: 12.5px;
  color: rgba(var(--v-theme-on-surface), 0.45);
  max-width: 340px;
  line-height: 1.6;
}

.empty-action {
  margin-top: 10px;
}
</style>
