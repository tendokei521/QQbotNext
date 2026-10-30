<script setup lang="ts">
/**
 * 「编辑系统提示词」：本地修改三套提示词方案（聊天 / 角色扮演 / 助手）的正文。
 *
 * 说明两点代价（界面上直说）：
 * 1. 覆盖是全局的，所有账号生效；账号级「行为规则覆盖」仍优先。
 * 2. 覆盖后正文变成静态文本，不再随账号能力（视觉/搜索/记忆/发言方式）自动调整。
 */
import { computed, ref, watch } from 'vue'
import { useNotifyStore } from '@/stores/notify'
import { usePromptPresetsStore } from '@/stores/promptPresets'
import { errorMessage } from '@/api/http'

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void }>()

const prompts = usePromptPresetsStore()
const notify = useNotifyStore()

const activeTab = ref('chat')
const draft = ref('')
const loading = ref(false)

const current = computed(() => prompts.presets.find((row) => row.key === activeTab.value) || null)
const charCount = computed(() => draft.value.length)

function syncDraft() {
  draft.value = current.value?.effective_text || ''
}

async function open() {
  loading.value = true
  try {
    await prompts.load()
    const first = prompts.presets[0]
    if (first) activeTab.value = first.key
    syncDraft()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    loading.value = false
  }
}

watch(
  () => activeTab.value,
  () => syncDraft(),
)

// 父组件把 modelValue 从 false 改成 true（含「提示词风格 → 编辑这三套文案」的深链）
// 不会触发 v-dialog 的 update:model-value，所以要在 prop 上自己监听一次。
watch(
  () => props.modelValue,
  (visible) => {
    if (visible) void open()
  },
)

async function save() {
  if (!current.value) return
  try {
    await prompts.save(current.value.key, draft.value)
    notify.push(`已保存「${current.value.label}」系统提示词`, 'success')
    syncDraft()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

async function restoreDefault() {
  if (!current.value) return
  try {
    await prompts.reset(current.value.key)
    notify.push('已恢复默认文案', 'success')
    syncDraft()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

function close() {
  emit('update:modelValue', false)
}
</script>

<template>
  <v-dialog
    :model-value="modelValue"
    max-width="900"
    scrollable
    @update:model-value="(v: boolean) => { if (v) open(); else close() }"
  >
    <v-card>
      <v-card-title class="d-flex align-center">
        <v-icon icon="mdi-text-box-edit-outline" class="mr-2" color="primary" />
        编辑系统提示词
        <v-spacer />
        <v-btn icon="mdi-close" variant="text" size="small" @click="close" />
      </v-card-title>

      <!-- scrollable 对话框里 v-card 是 flex 容器：不加 flex-shrink-0 时 tabs 会被压成一条线 -->
      <v-tabs v-model="activeTab" density="compact" class="flex-shrink-0">
        <v-tab v-for="row in prompts.presets" :key="row.key" :value="row.key">
          {{ row.label }}
          <v-icon v-if="row.customized" icon="mdi-pencil" size="14" class="ml-1" color="warning" />
        </v-tab>
      </v-tabs>

      <v-card-text>
        <v-progress-linear v-if="loading" indeterminate color="primary" class="mb-2" />
        <v-alert type="info" variant="tonal" density="compact" class="mb-3">
          <div class="text-caption">全局生效；账号级规则仍优先</div>
        </v-alert>

        <div v-if="current" class="text-caption mb-2" style="opacity: 0.7">{{ current.description }}</div>
        <v-textarea
          v-model="draft"
          label="系统提示词正文"
          rows="18"
          auto-grow
          density="comfortable"
          variant="outlined"
          hide-details
        />
        <div class="d-flex align-center mt-1">
          <span class="text-caption" style="opacity: 0.6">{{ charCount }} 字</span>
          <v-spacer />
          <v-btn size="x-small" variant="text" @click="draft = current?.default_text || ''">
            填入默认文案
          </v-btn>
        </div>
      </v-card-text>

      <v-card-actions>
        <v-btn variant="text" :disabled="!current?.customized" @click="restoreDefault">恢复默认</v-btn>
        <v-spacer />
        <v-btn variant="text" @click="close">取消</v-btn>
        <v-btn
          color="primary"
          variant="tonal"
          :loading="prompts.saving === activeTab"
          @click="save"
        >
          保存
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
