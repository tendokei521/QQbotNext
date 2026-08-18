<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import http, { errorMessage } from '@/api/http'
import { useNotifyStore } from '@/stores/notify'

interface ProviderPreset {
  id: string
  name: string
  provider: string
  config: Record<string, any>
  enabled: boolean
  created_at: number
  updated_at: number
}

interface ProviderModel {
  id: string
  preset_id: string
  model: string
  provider_type: string
  enabled: boolean
  config: Record<string, any>
  created_at: number
  updated_at: number
}

const notify = useNotifyStore()
const presets = ref<ProviderPreset[]>([])
const models = ref<ProviderModel[]>([])
const allModels = ref<ProviderModel[]>([])
const availableModels = ref<string[]>([])
const selectedPresetId = ref('')
const loadingPresets = ref(false)
const loadingModels = ref(false)
const fetchingModels = ref(false)
const testingId = ref('')
const savingSource = ref(false)
const savingModel = ref(false)

const sourceDialog = ref(false)
const editingSourceId = ref('')
const sourceForm = reactive({
  name: '',
  provider: 'openai',
  api_base: '',
  api_key: '',
  retry_attempts: 3,
  timeout: 30,
  enabled: true,
})

const modelDialog = ref(false)
const editingModelId = ref('')
const modelForm = reactive({
  preset_id: '',
  model: '',
  provider_type: 'chat',
  temperature: 0.7,
  max_tokens: 1024,
  enabled: true,
})

const settingsDialog = ref(false)
const settingsSaving = ref(false)
const settingsForm = reactive({
  default_preset_id: '',
  default_model_id: '',
  fallback_model_ids: '',
  provider_pool: '*',
})

const selectedPreset = computed(() => presets.value.find((p) => p.id === selectedPresetId.value))

async function loadPresets() {
  loadingPresets.value = true
  try {
    const res = await http.get<{ ok: boolean; presets: ProviderPreset[] }>('/api/provider-presets')
    presets.value = res.data?.presets || []
    if (!selectedPresetId.value && presets.value.length) {
      selectedPresetId.value = presets.value[0].id
    }
    if (selectedPresetId.value && !presets.value.some((p) => p.id === selectedPresetId.value)) {
      selectedPresetId.value = presets.value[0]?.id || ''
    }
    if (selectedPresetId.value) await loadModels()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    loadingPresets.value = false
  }
}

async function loadModels() {
  if (!selectedPresetId.value) {
    models.value = []
    return
  }
  loadingModels.value = true
  try {
    const res = await http.get<{ ok: boolean; models: ProviderModel[] }>('/api/provider-models', {
      params: { preset_id: selectedPresetId.value },
    })
    models.value = res.data?.models || []
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    loadingModels.value = false
  }
}

async function loadSettings() {
  try {
    const [settingsRes, modelsRes] = await Promise.all([
      http.get<{ ok: boolean; settings: any }>('/api/provider-settings'),
      http.get<{ ok: boolean; models: ProviderModel[] }>('/api/provider-models'),
    ])
    const s = settingsRes.data?.settings || {}
    allModels.value = modelsRes.data?.models || []
    Object.assign(settingsForm, {
      default_preset_id: s.default_preset_id || '',
      default_model_id: s.default_model_id || '',
      fallback_model_ids: Array.isArray(s.fallback_model_ids) ? s.fallback_model_ids.join(', ') : '',
      provider_pool: Array.isArray(s.provider_pool) ? s.provider_pool.join(', ') : (s.provider_pool || '*'),
    })
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

function openSettings() {
  loadSettings()
  settingsDialog.value = true
}

async function saveSettings() {
  settingsSaving.value = true
  try {
    await http.put('/api/provider-settings', {
      default_preset_id: settingsForm.default_preset_id,
      default_model_id: settingsForm.default_model_id,
      fallback_model_ids: settingsForm.fallback_model_ids
        .split(',')
        .map((s: string) => s.trim())
        .filter(Boolean),
      provider_pool: settingsForm.provider_pool
        .split(',')
        .map((s: string) => s.trim())
        .filter(Boolean),
    })
    notify.push('Provider 全局设置已保存', 'success')
    settingsDialog.value = false
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    settingsSaving.value = false
  }
}

function selectPreset(id: string) {
  selectedPresetId.value = id
  loadModels()
}

function openCreateSource() {
  editingSourceId.value = ''
  Object.assign(sourceForm, {
    name: '',
    provider: 'openai',
    api_base: '',
    api_key: '',
    retry_attempts: 3,
    timeout: 30,
    enabled: true,
  })
  sourceDialog.value = true
}

function openEditSource(preset: ProviderPreset) {
  editingSourceId.value = preset.id
  Object.assign(sourceForm, {
    name: preset.name,
    provider: preset.provider,
    api_base: preset.config.api_base || '',
    api_key: preset.config.api_key || '',
    retry_attempts: preset.config.retry_attempts || 3,
    timeout: preset.config.timeout || 30,
    enabled: preset.enabled,
  })
  sourceDialog.value = true
}

async function saveSource() {
  if (!sourceForm.name.trim() || !sourceForm.api_base.trim()) {
    notify.push('请填写名称与 API 基础 URL', 'warning')
    return
  }
  savingSource.value = true
  try {
    const config: Record<string, any> = {
      api_base: sourceForm.api_base.trim(),
      api_key: sourceForm.api_key.trim(),
      retry_attempts: Number(sourceForm.retry_attempts) || 3,
      timeout: Number(sourceForm.timeout) || 30,
    }
    const payload = {
      name: sourceForm.name.trim(),
      provider: sourceForm.provider,
      config,
      enabled: sourceForm.enabled,
    }
    if (editingSourceId.value) {
      await http.put(`/api/provider-presets/${editingSourceId.value}`, payload)
      notify.push('Provider 预设已更新', 'success')
    } else {
      const res = await http.post<{ preset: ProviderPreset }>('/api/provider-presets', payload)
      selectedPresetId.value = res.data?.preset?.id || ''
    }
    sourceDialog.value = false
    await loadPresets()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    savingSource.value = false
  }
}

async function deleteSource(preset: ProviderPreset) {
  if (!window.confirm(`确认删除连接预设「${preset.name}」？其下模型会一并删除，引用该预设的 Agent 将恢复为默认（空）。`)) return
  try {
    await http.delete(`/api/provider-presets/${preset.id}`)
    notify.push('Provider 预设已删除', 'success')
    if (selectedPresetId.value === preset.id) selectedPresetId.value = ''
    await loadPresets()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

function openCreateModel() {
  if (!selectedPresetId.value) return
  editingModelId.value = ''
  Object.assign(modelForm, {
    preset_id: selectedPresetId.value,
    model: '',
    provider_type: 'chat',
    temperature: 0.7,
    max_tokens: 1024,
    enabled: true,
  })
  modelDialog.value = true
}

function openEditModel(model: ProviderModel) {
  editingModelId.value = model.id
  Object.assign(modelForm, {
    preset_id: model.preset_id,
    model: model.model,
    provider_type: model.provider_type,
    temperature: model.config.temperature ?? 0.7,
    max_tokens: model.config.max_tokens ?? 1024,
    enabled: model.enabled,
  })
  modelDialog.value = true
}

async function saveModel() {
  if (!modelForm.model.trim()) {
    notify.push('请填写模型名称', 'warning')
    return
  }
  savingModel.value = true
  try {
    const payload = {
      preset_id: modelForm.preset_id,
      model: modelForm.model.trim(),
      provider_type: modelForm.provider_type,
      temperature: Number(modelForm.temperature) || 0.7,
      max_tokens: Number(modelForm.max_tokens) || 1024,
      enabled: modelForm.enabled,
    }
    if (editingModelId.value) {
      await http.put(`/api/provider-models/${editingModelId.value}`, payload)
      notify.push('模型实例已更新', 'success')
    } else {
      await http.post('/api/provider-models', payload)
      notify.push('模型实例已创建', 'success')
    }
    modelDialog.value = false
    await loadModels()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    savingModel.value = false
  }
}

async function fetchModels() {
  if (!selectedPresetId.value) return
  fetchingModels.value = true
  try {
    const res = await http.post<{ ok: boolean; models: string[] }>(
      `/api/provider-presets/${selectedPresetId.value}/models/fetch`,
      {},
    )
    const list = res.data?.models || []
    availableModels.value = list
    if (!list.length) {
      notify.push('没有拉取到模型，可手动添加', 'warning')
    } else {
      notify.push(`拉取到 ${list.length} 个模型，可用下拉选择`, 'success')
      // 预填第一个模型名，方便快速创建
      openCreateModel()
      modelForm.model = list[0]
    }
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    fetchingModels.value = false
  }
}

async function testModel(model: ProviderModel) {
  testingId.value = model.id
  try {
    const res = await http.post<{ ok: boolean; message?: string; reply?: string }>(
      `/api/provider-models/${model.id}/test`,
      {},
    )
    notify.push(`连接正常${res.data?.reply ? `：${res.data.reply}` : ''}`, 'success')
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    testingId.value = ''
  }
}

async function deleteModel(model: ProviderModel) {
  if (!window.confirm(`确认删除模型实例「${model.model}」？`)) return
  try {
    await http.delete(`/api/provider-models/${model.id}`)
    notify.push('模型实例已删除', 'success')
    await loadModels()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

async function testPreset(preset: ProviderPreset) {
  testingId.value = `preset:${preset.id}`
  try {
    const res = await http.post<{ ok: boolean; message?: string; reply?: string }>(
      `/api/provider-presets/${preset.id}/test`,
      {},
    )
    notify.push(`连接正常${res.data?.reply ? `：${res.data.reply}` : ''}`, 'success')
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    testingId.value = ''
  }
}

onMounted(loadPresets)
</script>

<template>
  <div>
    <div class="app-page-header" style="align-items: center">
      <div>
        <h1 class="app-page-title">Provider 预设</h1>
        <div class="app-page-subtitle">连接预设 + 模型实例两级管理，Agent 只需选择“预设 / 模型”</div>
      </div>
      <v-spacer />
      <v-btn variant="tonal" prepend-icon="mdi-tune" class="mr-1" @click="openSettings">全局设置</v-btn>
      <v-btn color="primary" variant="tonal" prepend-icon="mdi-plus" @click="openCreateSource">新建连接预设</v-btn>
    </div>

    <v-row class="mt-2">
      <v-col cols="12" md="4" lg="3">
        <v-card variant="outlined">
          <v-card-title class="d-flex align-center">
            <v-icon icon="mdi-api" class="mr-2" color="primary" /> 连接预设
          </v-card-title>
          <v-card-text class="pa-2">
            <v-progress-linear v-if="loadingPresets" indeterminate color="primary" />
            <v-list v-else density="compact">
              <v-list-item
                v-for="p in presets"
                :key="p.id"
                :active="selectedPresetId === p.id"
                @click="selectPreset(p.id)"
              >
                <template #prepend>
                  <v-icon :color="p.enabled ? 'primary' : ''" icon="mdi-server" />
                </template>
                <v-list-item-title>{{ p.name }}</v-list-item-title>
                <v-list-item-subtitle>{{ p.config.api_base || p.provider }}</v-list-item-subtitle>
                <template #append>
                  <v-btn size="x-small" variant="text" icon="mdi-pencil" @click.stop="openEditSource(p)" />
                  <v-btn size="x-small" variant="text" icon="mdi-delete" color="error" @click.stop="deleteSource(p)" />
                </template>
              </v-list-item>
              <v-list-item v-if="presets.length === 0">
                <v-list-item-title class="text-caption text-center">暂无连接预设</v-list-item-title>
              </v-list-item>
            </v-list>
          </v-card-text>
        </v-card>
      </v-col>

      <v-col cols="12" md="8" lg="9">
        <template v-if="selectedPreset">
          <v-card variant="outlined" class="mb-4">
            <v-card-title class="d-flex align-center">
              <v-icon icon="mdi-server" class="mr-2" color="primary" /> {{ selectedPreset.name }}
              <v-chip size="small" variant="tonal" class="ml-2">{{ selectedPreset.provider }}</v-chip>
              <v-spacer />
              <v-btn size="small" variant="tonal" prepend-icon="mdi-pencil" @click="openEditSource(selectedPreset)">编辑连接</v-btn>
              <v-btn size="small" variant="tonal" prepend-icon="mdi-test-tube" class="ml-1" :loading="testingId === `preset:${selectedPreset.id}`" @click="testPreset(selectedPreset)">测试连接</v-btn>
            </v-card-title>
            <v-card-text class="text-caption">
              API Base：{{ selectedPreset.config.api_base || '—' }} · 重试：{{ selectedPreset.config.retry_attempts || 3 }} · 超时：{{ selectedPreset.config.timeout || 30 }}s
            </v-card-text>
          </v-card>

          <v-card variant="outlined">
            <v-card-title class="d-flex align-center">
              <v-icon icon="mdi-creation" class="mr-2" color="primary" /> 模型实例
              <v-spacer />
              <v-btn size="small" variant="tonal" prepend-icon="mdi-download" :loading="fetchingModels" @click="fetchModels">拉取模型</v-btn>
              <v-btn size="small" color="primary" variant="tonal" prepend-icon="mdi-plus" class="ml-1" @click="openCreateModel">添加模型</v-btn>
            </v-card-title>
            <v-card-text>
              <v-progress-linear v-if="loadingModels" indeterminate color="primary" />
              <v-table v-else density="comfortable">
                <thead>
                  <tr>
                    <th>模型</th>
                    <th>类型</th>
                    <th>温度</th>
                    <th>Max Tokens</th>
                    <th>状态</th>
                    <th class="text-right">操作</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-if="models.length === 0">
                    <td colspan="6" class="text-center text-caption pa-6">该连接下还没有模型实例，请拉取或手动添加</td>
                  </tr>
                  <tr v-for="m in models" :key="m.id">
                    <td class="font-weight-medium">{{ m.model }}</td>
                    <td><v-chip size="x-small" variant="tonal">{{ m.provider_type }}</v-chip></td>
                    <td class="text-caption">{{ m.config.temperature ?? 0.7 }}</td>
                    <td class="text-caption">{{ m.config.max_tokens ?? 1024 }}</td>
                    <td>
                      <v-chip size="x-small" :color="m.enabled ? 'success' : 'default'" variant="tonal">
                        {{ m.enabled ? '启用' : '停用' }}
                      </v-chip>
                    </td>
                    <td class="text-right">
                      <v-btn size="x-small" variant="text" color="primary" :loading="testingId === m.id" @click="testModel(m)">测试</v-btn>
                      <v-btn size="x-small" variant="text" color="primary" @click="openEditModel(m)">编辑</v-btn>
                      <v-btn size="x-small" variant="text" color="error" @click="deleteModel(m)">删除</v-btn>
                    </td>
                  </tr>
                </tbody>
              </v-table>
            </v-card-text>
          </v-card>
        </template>

        <v-card v-else variant="outlined" class="pa-8 text-center text-caption">
          请先在左侧创建连接预设
        </v-card>
      </v-col>
    </v-row>

    <!-- 连接预设编辑 -->
    <v-dialog v-model="sourceDialog" max-width="520">
      <v-card>
        <v-card-title class="d-flex align-center">
          <v-icon icon="mdi-api" class="mr-2" color="primary" />
          {{ editingSourceId ? '编辑连接预设' : '新建连接预设' }}
        </v-card-title>
        <v-card-text>
          <v-text-field v-model="sourceForm.name" label="名称" placeholder="例如：DeepSeek 主账号" density="comfortable" hide-details class="mb-3" />
          <v-select
            v-model="sourceForm.provider"
            :items="[
              { title: 'OpenAI 兼容（DeepSeek/中转/Ollama 等）', value: 'openai' },
              { title: 'DeepSeek（兼容）', value: 'deepseek' },
              { title: 'OpenRouter（兼容）', value: 'openrouter' },
              { title: 'Ollama（兼容）', value: 'ollama' },
            ]"
            label="Provider 类型"
            density="comfortable"
            hide-details
            class="mb-3"
          />
          <v-text-field v-model="sourceForm.api_base" label="API 基础 URL" placeholder="https://api.deepseek.com" density="comfortable" hide-details class="mb-3" />
          <v-text-field
            v-model="sourceForm.api_key"
            label="API Key"
            placeholder="sk-..."
            type="password"
            density="comfortable"
            hide-details
            class="mb-3"
          />
          <v-row>
            <v-col cols="6">
              <v-text-field v-model.number="sourceForm.retry_attempts" label="最大重试次数" type="number" min="1" max="6" density="comfortable" hide-details />
            </v-col>
            <v-col cols="6">
              <v-text-field v-model.number="sourceForm.timeout" label="超时(秒)" type="number" min="5" max="120" density="comfortable" hide-details />
            </v-col>
          </v-row>
          <v-switch v-model="sourceForm.enabled" label="启用" color="primary" density="compact" hide-details class="mt-2" />
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="sourceDialog = false">取消</v-btn>
          <v-btn color="primary" variant="tonal" :loading="savingSource" @click="saveSource">保存</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 模型编辑 -->
    <v-dialog v-model="modelDialog" max-width="480">
      <v-card>
        <v-card-title class="d-flex align-center">
          <v-icon icon="mdi-creation" class="mr-2" color="primary" />
          {{ editingModelId ? '编辑模型实例' : '添加模型实例' }}
        </v-card-title>
        <v-card-text>
          <v-select
            v-if="availableModels.length"
            v-model="modelForm.model"
            :items="availableModels"
            label="可选模型（拉取结果）"
            placeholder="选择后同步到模型名称"
            density="comfortable"
            hide-details
            clearable
            class="mb-3"
          />
          <v-text-field v-model="modelForm.model" label="模型名称" placeholder="deepseek-chat" density="comfortable" hide-details class="mb-3" />
          <v-select
            v-model="modelForm.provider_type"
            :items="[
              { title: '聊天/对话', value: 'chat' },
              { title: 'Embedding', value: 'embedding' },
              { title: 'Rerank', value: 'rerank' },
              { title: 'TTS', value: 'tts' },
              { title: 'STT', value: 'stt' },
            ]"
            label="能力类型"
            density="comfortable"
            hide-details
            class="mb-3"
          />
          <v-row>
            <v-col cols="6">
              <v-text-field v-model.number="modelForm.temperature" label="温度" type="number" step="0.1" min="0" max="2" density="comfortable" hide-details />
            </v-col>
            <v-col cols="6">
              <v-text-field v-model.number="modelForm.max_tokens" label="最大输出 Token" type="number" min="1" max="65536" density="comfortable" hide-details />
            </v-col>
          </v-row>
          <v-switch v-model="modelForm.enabled" label="启用" color="primary" density="compact" hide-details class="mt-2" />
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="modelDialog = false">取消</v-btn>
          <v-btn color="primary" variant="tonal" :loading="savingModel" @click="saveModel">保存</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 全局设置 -->
    <v-dialog v-model="settingsDialog" max-width="520">
      <v-card>
        <v-card-title class="d-flex align-center">
          <v-icon icon="mdi-tune" class="mr-2" color="primary" /> Provider 全局设置
        </v-card-title>
        <v-card-text>
          <v-select
            v-model="settingsForm.default_preset_id"
            :items="presets.map((p) => ({ title: p.name, value: p.id }))"
            label="默认连接预设"
            density="comfortable"
            hide-details
            clearable
            class="mb-3"
          />
          <v-select
            v-model="settingsForm.default_model_id"
            :items="allModels.map((m) => ({ title: `${m.model} (${m.id})`, value: m.id }))"
            label="默认模型实例"
            density="comfortable"
            hide-details
            clearable
            class="mb-3"
          />
          <v-text-field v-model="settingsForm.fallback_model_ids" label="Fallback 模型 ID（逗号分隔）" density="comfortable" hide-details class="mb-3" />
          <v-text-field v-model="settingsForm.provider_pool" label="Provider Pool（逗号分隔，* 表示全部）" density="comfortable" hide-details />
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="settingsDialog = false">取消</v-btn>
          <v-btn color="primary" variant="tonal" :loading="settingsSaving" @click="saveSettings">保存</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>