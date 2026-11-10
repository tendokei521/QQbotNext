<script setup lang="ts">
/**
 * 能力闸门：账号类型不支持某个方法时，渲染禁用态 + tooltip 说明，
 * 不渲染默认插槽（按钮根本点不到，不存在「点了一下才报错」）。
 *
 * 页面只传 `allowed`（来自 `useBotCapabilities().supports(...)`），不判断协议名。
 */
const props = withDefaults(
  defineProps<{
    allowed: boolean
    /** 不支持时的说明，默认「该类型不支持使用此方法」 */
    text?: string
  }>(),
  { text: '该类型不支持使用此方法' },
)
</script>

<template>
  <v-tooltip v-if="!props.allowed" :text="props.text" location="top" max-width="300">
    <template #activator="{ props: tipProps }">
      <div v-bind="tipProps" class="capability-gate capability-gate--off">
        <v-icon icon="mdi-lock-outline" size="15" />
        <span class="capability-gate__text">{{ props.text }}</span>
      </div>
    </template>
  </v-tooltip>
  <slot v-else />
</template>

<style scoped>
.capability-gate--off {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 100%;
  padding: 4px 10px;
  border-radius: 8px;
  border: 1px dashed rgba(var(--v-theme-on-surface), 0.24);
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-size: 12.5px;
  line-height: 1.5;
  cursor: not-allowed;
  user-select: none;
}

.capability-gate__text {
  overflow-wrap: anywhere;
}
</style>
