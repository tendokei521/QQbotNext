<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import http, { errorMessage } from '@/api/http'
import { useNotifyStore } from '@/stores/notify'
import { useAgentConfigStore } from '@/stores/agentConfig'

interface ProfileItem {
  id: string
  name: string
  config: Record<string, any>
  updated_at: number
}

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{ (e: 'update:modelValue', value: boolean): void }>()

const agent = useAgentConfigStore()
const notify = useNotifyStore()

const profiles = ref<ProfileItem[]>([])
const loading = ref(false)
const saving = ref(false)
const applyingId = ref('')
const deletingId = ref('')
const newName = ref('')

async function loadProfiles() {
  loading.value = true
  try {
    const res = await http.get<{ ok: boolean; profiles: ProfileItem[] }>('/api/config-profiles')
    profiles.value = res.data.profiles || []
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    loading.value = false
  }
}

async function saveCurrentTemplate() {
  if (!newName.value.trim()) {
    notify.push('请输入模板名称', 'warning')
    return
  }
  saving.value = true
  try {
    await http.post('/api/config-profiles', {
      name: newName.value.trim(),
      config: { ...agent.draft },
    })
    notify.push('已保存为配置模板', 'success')
    newName.value = ''
    await loadProfiles()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    saving.value = false
  }
}

async function applyProfile(profile: ProfileItem) {
  if (applyingId.value) return
  applyingId.value = profile.id
  try {
    const res = await http.get<{ ok: boolean; profile: ProfileItem }>(`/api/config-profiles/${profile.id}`)
    agent.applyConfig(res.data.profile?.config || {})
    notify.push(`已应用模板「${profile.name}」`, 'success')
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    applyingId.value = ''
  }
}

async function deleteProfile(profile: ProfileItem) {
  if (deletingId.value) return
  if (!window.confirm(`确认删除模板「${profile.name}」？`)) return
  deletingId.value = profile.id
  try {
    await http.delete(`/api/config-profiles/${profile.id}`)
    notify.push('模板已删除', 'success')
    await loadProfiles()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    deletingId.value = ''
  }
}

watch(
  () => props.modelValue,
  (open) => {
    if (open) loadProfiles()
  },
)

onMounted(() => {
  if (props.modelValue) loadProfiles()
})
</script>

<template>
  <v-dialog :model-value="props.modelValue" max-width="560" @update:model-value="emit('update:modelValue', $event)">
    <v-card>
      <v-card-title class="d-flex align-center">
        <v-icon icon="mdi-book-multiple" class="mr-2" color="primary" /> Agent 配置模板
        <v-spacer />
        <v-btn icon="mdi-close" variant="text" size="small" @click="emit('update:modelValue', false)" />
      </v-card-title>
      <v-card-text>
        <div class="d-flex gap-2 align-center mb-4">
          <v-text-field
            v-model="newName"
            label="模板名称"
            density="comfortable"
            variant="outlined"
            hide-details
          />
          <v-btn color="primary" variant="tonal" :loading="saving" @click="saveCurrentTemplate">保存当前配置</v-btn>
        </div>

        <v-progress-linear v-if="loading" indeterminate color="primary" />
        <v-list v-else density="compact">
          <v-list-item v-for="p in profiles" :key="p.id">
            <v-list-item-title>{{ p.name }}</v-list-item-title>
            <v-list-item-subtitle>
              {{ Object.keys(p.config || {}).length }} 个配置项 · 更新于 {{ new Date(p.updated_at * 1000).toLocaleString('zh-CN', { hour12: false }) }}
            </v-list-item-subtitle>
            <template #append>
              <v-btn
                size="x-small"
                variant="text"
                icon="mdi-close"
                color="error"
                title="删除模板"
                :disabled="deletingId !== ''"
                :loading="deletingId === p.id"
                @click.stop="deleteProfile(p)"
              />
              <v-btn size="small" variant="tonal" prepend-icon="mdi-arrow-up-bold-circle" :loading="applyingId === p.id" @click="applyProfile(p)">
                应用
              </v-btn>
            </template>
          </v-list-item>
          <v-list-item v-if="!profiles.length && !loading">
            <v-list-item-title class="text-caption text-center py-3" style="opacity: 0.5">
              暂无模板，可先保存当前配置
            </v-list-item-title>
          </v-list-item>
        </v-list>
      </v-card-text>
    </v-card>
  </v-dialog>
</template>
