<script setup lang="ts">
/**
 * 统一的「保存状态条」：状态 chip +（可选）保存按钮 + 一句保存方式说明。
 *
 * 各页面此前有四种呈现：面板是「保存按钮 + 已保存 chip + 自动保存说明」、
 * AI 设置是「保存配置按钮但成功后无反馈」、回复与权限只有 chip、模块页是
 * 「已保存 时间」。同一个问题（我刚改的存了吗）要在每页重新猜。
 *
 * 这里只做**展示**：状态由调用方传，保存按钮的点击抛给调用方。
 * 不读 store，避免各页面保存机制不同（agentConfig / modules / 手写）被强行统一。
 */
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    /** 保存状态；`saved` 只是短暂提示，长期态用 `clean` */
    status: 'clean' | 'dirty' | 'saving' | 'error' | 'blocked' | 'saved'
    /** 是否自动保存：true 时不显示保存按钮，只显示「改动会自动保存」 */
    autosave?: boolean
    /** 说明文案；空串表示不显示 */
    hint?: string
    saveLabel?: string
  }>(),
  {
    autosave: false,
    hint: '',
    saveLabel: '保存修改',
  },
)

const emit = defineEmits<{ save: [] }>()

const VIEW = {
  clean: { text: '已保存', color: 'success', icon: 'mdi-check-circle-outline' },
  saved: { text: '已保存', color: 'success', icon: 'mdi-check-circle-outline' },
  dirty: { text: '未保存', color: 'warning', icon: 'mdi-circle-edit-outline' },
  saving: { text: '保存中', color: 'info', icon: 'mdi-progress-clock' },
  error: { text: '保存失败', color: 'error', icon: 'mdi-alert-circle-outline' },
  blocked: { text: '无法保存', color: 'warning', icon: 'mdi-alert-circle-outline' },
} as const

const view = computed(() => VIEW[props.status])
// 常驻的「已保存」和「改动会自动保存」都是噪音：清态不显示任何提示，
// 只有保存成功后的短暂 saved、未保存/保存中/失败才占位。
const showChip = computed(() => props.status !== 'clean')
</script>

<template>
  <div class="save-bar">
    <v-chip
      v-if="showChip"
      size="small"
      :color="view.color"
      variant="tonal"
      :prepend-icon="view.icon"
      :title="status === 'blocked' ? '当前没有可用账号，改动无处保存' : view.text"
    >
      {{ view.text }}
    </v-chip>
    <v-btn
      v-if="!autosave && status !== 'saved'"
      size="small"
      variant="tonal"
      color="primary"
      prepend-icon="mdi-content-save-outline"
      :loading="status === 'saving'"
      @click="emit('save')"
    >
      {{ saveLabel }}
    </v-btn>
    <span v-if="hint" class="save-hint">{{ hint }}</span>
  </div>
</template>

<style scoped>
.save-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
}

.save-hint {
  font-size: 12px;
  color: rgba(var(--v-theme-on-surface), 0.55);
}
</style>
