<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import type { PermissionConfig } from '@/stores/modules'

const props = defineProps<{
  modelValue: PermissionConfig
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: PermissionConfig): void
}>()

const local = reactive<PermissionConfig>({
  group_mode: props.modelValue?.group_mode || 'blacklist',
  group_list: [...(props.modelValue?.group_list || [])],
  user_mode: props.modelValue?.user_mode || 'blacklist',
  user_list: [...(props.modelValue?.user_list || [])],
})

const groupText = ref(local.group_list.join('\n'))
const userText = ref(local.user_list.join('\n'))

function listsEqual(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((x, i) => x === b[i])
}

watch(
  () => props.modelValue,
  (v) => {
    if (!v) return
    const modeChanged = (v.group_mode || 'blacklist') !== local.group_mode
    const listsChanged =
      !listsEqual(v.group_list || [], local.group_list) ||
      !listsEqual(v.user_list || [], local.user_list)
    local.group_mode = v.group_mode || 'blacklist'
    local.group_list = [...(v.group_list || [])]
    local.user_mode = v.user_mode || 'blacklist'
    local.user_list = [...(v.user_list || [])]
    // 仅外部变化才重写文本区（本组件回显 == 当前 local，跳过），
    // 避免每次按键触发重排、剥掉空行/移动光标
    if (modeChanged || listsChanged) {
      groupText.value = local.group_list.join('\n')
      userText.value = local.user_list.join('\n')
    }
  },
)

function emitChange() {
  emit('update:modelValue', {
    group_mode: local.group_mode,
    group_list: [...local.group_list],
    user_mode: local.user_mode,
    user_list: [...local.user_list],
  })
}

function onGroupText(v: string) {
  groupText.value = v
  local.group_list = v.split('\n').map((s) => s.trim()).filter(Boolean)
  emitChange()
}

function onUserText(v: string) {
  userText.value = v
  local.user_list = v.split('\n').map((s) => s.trim()).filter(Boolean)
  emitChange()
}

function onModeChange() {
  emitChange()
}
</script>

<template>
  <div class="permission-editor">
    <v-row>
      <v-col cols="12" md="6">
        <div class="field-label">群组模式</div>
        <v-select
          :model-value="local.group_mode"
          :items="[
            { title: '白名单（仅响应以下）', value: 'whitelist' },
            { title: '黑名单（不响应以下）', value: 'blacklist' },
          ]"
          density="comfortable"
          variant="outlined"
          hide-details
          @update:model-value="(v: string) => { local.group_mode = v as 'whitelist' | 'blacklist'; onModeChange() }"
        />
        <v-textarea
          :model-value="groupText"
          label="群号，每行一个"
          rows="4"
          density="comfortable"
          variant="outlined"
          class="mt-2"
          auto-grow
          @update:model-value="onGroupText"
        />
      </v-col>
      <v-col cols="12" md="6">
        <div class="field-label">用户模式</div>
        <v-select
          :model-value="local.user_mode"
          :items="[
            { title: '白名单（仅响应以下）', value: 'whitelist' },
            { title: '黑名单（不响应以下）', value: 'blacklist' },
          ]"
          density="comfortable"
          variant="outlined"
          hide-details
          @update:model-value="(v: string) => { local.user_mode = v as 'whitelist' | 'blacklist'; onModeChange() }"
        />
        <v-textarea
          :model-value="userText"
          label="QQ号，每行一个"
          rows="4"
          density="comfortable"
          variant="outlined"
          class="mt-2"
          auto-grow
          @update:model-value="onUserText"
        />
      </v-col>
    </v-row>
  </div>
</template>

<style scoped>
.permission-editor {
  width: 100%;
}

.field-label {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 6px;
}
</style>
