<script setup lang="ts">
/**
 * 统一的确认弹窗：替代散落各处的 `window.confirm`（样式旧、移动端难受、说不清后果）。
 *
 * 用法：
 * ```vue
 * <ConfirmDialog v-model="open" title="删除插件" :details="['会删除插件文件', '该账号配置保留']"
 *                danger confirm-text="删除" @confirm="doDelete" />
 * ```
 * 只做展示与确认，业务动作由 `confirm` 事件执行；`loading` 由调用方控制。
 */
withDefaults(
  defineProps<{
    modelValue: boolean
    title: string
    message?: string
    /** 影响清单：比一句「不可撤销」更能让人看清楚自己在删什么 */
    details?: string[]
    danger?: boolean
    confirmText?: string
    cancelText?: string
    loading?: boolean
  }>(),
  {
    message: '',
    details: () => [],
    danger: false,
    confirmText: '确定',
    cancelText: '取消',
    loading: false,
  },
)

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  confirm: []
}>()

function close() {
  emit('update:modelValue', false)
}
</script>

<template>
  <v-dialog
    :model-value="modelValue"
    max-width="460"
    persistent
    @update:model-value="emit('update:modelValue', $event)"
  >
    <v-card>
      <v-card-title class="d-flex align-center">
        <v-icon
          :icon="danger ? 'mdi-alert-outline' : 'mdi-help-circle-outline'"
          :color="danger ? 'error' : 'primary'"
          class="mr-2"
        />
        {{ title }}
      </v-card-title>
      <v-card-text>
        <p v-if="message" class="text-body-2 mb-2">{{ message }}</p>
        <ul v-if="details.length" class="confirm-details">
          <li v-for="item in details" :key="item">{{ item }}</li>
        </ul>
        <slot />
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn variant="text" :disabled="loading" @click="close">{{ cancelText }}</v-btn>
        <v-btn variant="flat" :color="danger ? 'error' : 'primary'" :loading="loading" @click="emit('confirm')">
          {{ confirmText }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.confirm-details {
  margin: 8px 0 0;
  padding-left: 20px;
  font-size: 13px;
  line-height: 1.7;
  color: rgba(var(--v-theme-on-surface), 0.72);
}
</style>
