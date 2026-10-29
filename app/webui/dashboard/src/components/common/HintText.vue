<script setup lang="ts">
/**
 * 统一的字段说明文字。
 *
 * 之前各页各写一份 `.field-desc`（字号 12~12.5、透明度 0.5~0.75 混用），
 * 扫视时形不成层级。所有「说明/帮助」都走这里，颜色与行高只在一处维护。
 */
withDefaults(
  defineProps<{
    /** 说明文字；为空时不渲染（可改用默认插槽放自定义内容） */
    text?: string
    /** 可选的最大宽度（如 '420px'），避免说明行过长 */
    maxWidth?: string
  }>(),
  { text: '', maxWidth: '' },
)
</script>

<template>
  <div v-if="text" class="app-hint" :style="maxWidth ? { maxWidth } : undefined">{{ text }}</div>
  <slot v-else />
</template>

<style scoped>
.app-hint {
  font-size: 12.5px;
  line-height: 1.6;
  color: rgba(var(--v-theme-on-surface), 0.6);
  white-space: pre-wrap;
}
</style>
