<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import http, { errorMessage } from '@/api/http'
import AgentSubPage from '@/components/agent/AgentSubPage.vue'
import { useAgentConfigStore } from '@/stores/agentConfig'
import { useNotifyStore } from '@/stores/notify'

interface RolePreset {
  id: string
  name: string
  description: string
  system_prompt: string
  created_at: number
  updated_at: number
}

const agent = useAgentConfigStore()
const notify = useNotifyStore()

const presets = ref<RolePreset[]>([])
const loading = ref(false)
const saving = ref(false)
const search = ref('')
const selectedId = ref('')
const editingId = ref('')
const form = reactive({
  name: '',
  description: '',
  system_prompt: '',
})

const filteredPresets = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return presets.value
  return presets.value.filter((p) =>
    [p.name, p.description, p.system_prompt].some((v) => (v || '').toLowerCase().includes(q)),
  )
})

const selectedPreset = computed(() => presets.value.find((p) => p.id === selectedId.value) || null)

function fillForm(p: RolePreset) {
  Object.assign(form, {
    name: p.name,
    description: p.description,
    system_prompt: p.system_prompt,
  })
}

function openPreset(p: RolePreset) {
  selectedId.value = p.id
  editingId.value = p.id
  fillForm(p)
}

function startNew() {
  selectedId.value = ''
  editingId.value = ''
  Object.assign(form, {
    name: '',
    description: '',
    system_prompt: '',
  })
}

async function loadPresets() {
  loading.value = true
  try {
    const res = await http.get<{ ok: boolean; presets: RolePreset[] }>('/api/role-presets')
    presets.value = res.data?.presets || []
    if (selectedId.value && !presets.value.some((p) => p.id === selectedId.value)) {
      selectedId.value = ''
      editingId.value = ''
    }
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    loading.value = false
  }
}

async function savePreset() {
  if (!form.name.trim()) {
    notify.push('请填写角色名称', 'warning')
    return
  }
  if (!form.system_prompt.trim()) {
    notify.push('请填写系统提示词', 'warning')
    return
  }
  saving.value = true
  try {
    const payload = {
      name: form.name.trim(),
      description: form.description.trim(),
      system_prompt: form.system_prompt,
    }
    let saved: RolePreset | undefined
    if (editingId.value) {
      const res = await http.put<{ ok: boolean; preset: RolePreset }>(`/api/role-presets/${editingId.value}`, payload)
      saved = res.data?.preset
      notify.push('角色预设已更新', 'success')
    } else {
      const res = await http.post<{ ok: boolean; preset: RolePreset }>('/api/role-presets', payload)
      saved = res.data?.preset
      notify.push('角色预设已创建', 'success')
    }
    await loadPresets()
    if (saved?.id) {
      selectedId.value = saved.id
      editingId.value = saved.id
      fillForm(saved)
    }
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    saving.value = false
  }
}

async function deletePreset(p: RolePreset) {
  if (!window.confirm(`确认删除角色预设「${p.name}」？当前 Agent 已应用的提示词不会被清除。`)) return
  try {
    await http.delete(`/api/role-presets/${p.id}`)
    notify.push('角色预设已删除', 'success')
    if (selectedId.value === p.id) {
      selectedId.value = ''
      editingId.value = ''
    }
    await loadPresets()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

async function applyToAgent() {
  if (!selectedPreset.value) return
  if (agent.botId == null) {
    notify.push('请先选择并连接一个 Bot', 'warning')
    return
  }
  agent.onChange('system_prompt', selectedPreset.value.system_prompt)
  await agent.save()
  notify.push(`已应用角色「${selectedPreset.value.name}」`, 'success')
}

function saveCurrentAsPreset() {
  if (agent.botId == null) {
    notify.push('请先选择并连接一个 Bot', 'warning')
    return
  }
  if (!agent.draft.system_prompt?.trim()) {
    notify.push('当前 Agent 没有可保存的提示词', 'warning')
    return
  }
  startNew()
  form.system_prompt = agent.draft.system_prompt
  notify.push('已载入当前提示词，填写角色名称后保存', 'info')
}

onMounted(() => {
  agent.load()
  loadPresets()
})
</script>

<template>
  <AgentSubPage
    title="角色设定"
    subtitle="角色预设管理：左侧选择/搜索，右侧编辑并应用到当前 Agent"
    icon="mdi-account-heart"
    color="pink"
  >
    <v-row class="mt-2">
      <v-col cols="12" md="4">
        <v-card variant="outlined" class="h-full">
          <v-card-title class="d-flex align-center">
            <v-icon icon="mdi-account-group-outline" class="mr-2" color="pink" /> 角色预设
            <v-spacer />
            <v-btn size="small" color="primary" variant="tonal" prepend-icon="mdi-plus" @click="startNew">
              新建
            </v-btn>
          </v-card-title>
          <v-card-text class="pa-2">
            <v-text-field
              v-model="search"
              prepend-inner-icon="mdi-magnify"
              label="搜索角色"
              density="compact"
              variant="outlined"
              hide-details
              class="mb-2"
            />
            <v-progress-linear v-if="loading" indeterminate color="primary" />
            <template v-else-if="!presets.length">
              <div class="text-caption text-center pa-4">暂无角色预设，点击「新建」创建</div>
            </template>
            <v-list v-else density="compact">
              <v-list-item
                v-for="p in filteredPresets"
                :key="p.id"
                :active="selectedId === p.id"
                @click="openPreset(p)"
              >
                <template #prepend>
                  <v-icon icon="mdi-account-heart" color="pink" />
                </template>
                <v-list-item-title class="text-body-2">{{ p.name }}</v-list-item-title>
                <v-list-item-subtitle class="text-caption">
                  {{ p.description || p.system_prompt.slice(0, 30) || '无描述' }}
                </v-list-item-subtitle>
                <template #append>
                  <v-btn
                    size="x-small"
                    variant="text"
                    icon="mdi-delete"
                    color="error"
                    title="删除角色"
                    @click.stop="deletePreset(p)"
                  />
                </template>
              </v-list-item>
              <v-list-item v-if="!filteredPresets.length">
                <v-list-item-title class="text-caption text-center py-3" style="opacity: 0.5">
                  无匹配角色
                </v-list-item-title>
              </v-list-item>
            </v-list>
          </v-card-text>
        </v-card>
      </v-col>

      <v-col cols="12" md="8">
        <v-card variant="outlined" class="mb-4">
          <v-card-title class="d-flex align-center">
            <v-icon icon="mdi-pencil-outline" class="mr-2" color="primary" />
            {{ editingId ? '编辑角色' : '新建角色' }}
            <v-spacer />
            <v-chip v-if="selectedPreset" size="small" variant="tonal" color="pink">
              {{ selectedPreset.name }}
            </v-chip>
          </v-card-title>
          <v-card-text>
            <v-row>
              <v-col cols="12" md="6">
                <v-text-field
                  v-model="form.name"
                  label="角色名称"
                  density="comfortable"
                  variant="outlined"
                  hide-details
                  placeholder="例如：元气少女"
                />
              </v-col>
              <v-col cols="12" md="6">
                <v-text-field
                  v-model="form.description"
                  label="角色描述（一句话）"
                  density="comfortable"
                  variant="outlined"
                  hide-details
                />
              </v-col>
            </v-row>
            <v-textarea
              v-model="form.system_prompt"
              label="系统提示词"
              auto-grow
              rows="14"
              variant="outlined"
              class="mt-3"
              spellcheck="false"
            />
            <div class="d-flex flex-wrap gap-3 mt-3">
              <v-btn
                color="primary"
                variant="tonal"
                prepend-icon="mdi-content-save"
                :loading="saving"
                @click="savePreset"
              >
                保存角色
              </v-btn>
              <v-btn
                variant="tonal"
                color="pink"
                prepend-icon="mdi-arrow-right-bold-circle-outline"
                :disabled="!selectedPreset || agent.botId == null"
                @click="applyToAgent"
              >
                应用到当前 Agent
              </v-btn>
              <v-btn
                variant="tonal"
                prepend-icon="mdi-account-plus-outline"
                :disabled="agent.botId == null"
                @click="saveCurrentAsPreset"
              >
                从当前 Agent 另存
              </v-btn>
            </div>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>
  </AgentSubPage>
</template>