<script setup lang="ts">
/**
 * 插件状态条：插件加载失败 / 与其它插件冲突 / 文件不完整时，说明原因并给出下一步。
 *
 * 列表页、插件配置页共用；「检查依赖」按钮由调用方处理（打开依赖对话框）。
 */
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    status?: string
    error?: string
    /** 插件显示名（用于提示语） */
    name?: string
  }>(),
  {
    status: 'ok',
    error: '',
    name: '',
  },
)

const emit = defineEmits<{
  reload: []
  checkDeps: []
}>()

const kind = computed(() => {
  if (props.status === 'error' || props.error) return 'error'
  if (props.status === 'conflict') return 'conflict'
  if (props.status === 'manifest_missing') return 'manifest'
  return ''
})

const title = computed(() => {
  if (kind.value === 'error') return '插件加载失败'
  if (kind.value === 'conflict') return '与其它插件冲突'
  return '插件文件不完整'
})

const reason = computed(() => {
  if (props.error) return props.error
  if (kind.value === 'conflict') return '同一功能被多个插件占用，先停用其中一个再重新加载。'
  if (kind.value === 'manifest') return '缺少说明文件，版本与显示名用了默认值，一般不影响使用。'
  return '常见原因是缺少运行组件，可以检查依赖后重新加载。'
})

const alertType = computed(() => {
  if (kind.value === 'error') return 'error'
  if (kind.value === 'conflict') return 'warning'
  return 'info'
})
</script>

<template>
  <v-alert v-if="kind" :type="alertType" variant="tonal" density="compact" class="mb-3">
    <div class="text-caption font-weight-medium">{{ title }}<template v-if="name">：{{ name }}</template></div>
    <div class="text-caption mt-1">{{ reason }}</div>
    <div class="d-flex flex-wrap gap-2 mt-2">
      <v-btn size="small" variant="text" prepend-icon="mdi-refresh" @click="emit('reload')">重新加载</v-btn>
      <v-btn
        v-if="kind === 'error'"
        size="small"
        variant="text"
        prepend-icon="mdi-package-variant"
        @click="emit('checkDeps')"
      >
        检查依赖
      </v-btn>
    </div>
  </v-alert>
</template>
