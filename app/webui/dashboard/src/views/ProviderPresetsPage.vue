<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import http, { errorMessage } from '@/api/http'
import { useNotifyStore } from '@/stores/notify'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'

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
  /** 后端算好的「当下真正会用多少、来源是哪一层」（只含预设+模型两层） */
  effective?: {
    max_tokens: number
    max_tokens_origin: string
    max_tokens_notes: string[]
    context_window: number
    reasoning: boolean
    overrides: string[]
  }
  created_at: number
  updated_at: number
}

/** 内置预设模板（抄自 DSH 的 provider catalog，后端只读，不落库） */
interface BuiltinPreset {
  key: string
  name: string
  provider: string
  protocol: string
  category: string
  api_base: string
  env: string
  models: string[]
  note: string
  docs: string
  headers?: Record<string, string>
  /** 已落库的同端点预设 id；非空 = 已添加 */
  installed_preset_id: string
}

const MODALITY_OPTIONS = [
  { title: '文本', value: 'text' },
  { title: '图像', value: 'image' },
  { title: '音频/语音', value: 'audio' },
  { title: '工具调用', value: 'tool_use' },
]
const MODALITY_LABELS: Record<string, string> = {
  text: '文本',
  image: '图像',
  audio: '音频/语音',
  tool_use: '工具调用',
}

/**
 * 「自定义预设」只让你选四种 API 格式——和成熟客户端的自定义 API 配置一致：
 * 格式必须和端点实际支持的协议对得上，选错就是 404/400，而不是「玄学连不上」。
 */
const API_FORMAT_OPTIONS = [
  {
    title: 'Anthropic Messages（原生）',
    value: 'anthropic',
    props: { subtitle: 'Anthropic / MiniMax 等兼容接口' },
  },
  {
    title: 'OpenAI Chat Completions',
    value: 'openai',
    props: { subtitle: '最常用；DeepSeek、中转服务、Ollama 都选它' },
  },
  {
    title: 'OpenAI Responses API',
    value: 'openai-responses',
    props: { subtitle: 'OpenAI 的新版接口；有些新模型只支持它' },
  },
  {
    title: 'Gemini Native generateContent',
    value: 'gemini',
    props: { subtitle: 'Google Gemini 官方接口' },
  },
]

/** 模型类型 → 用户看得懂的名字 */
const PROVIDER_TYPE_LABELS: Record<string, string> = {
  chat: '对话',
  embedding: '文本向量',
  rerank: '重排',
  tts: '语音合成',
  stt: '语音识别',
}

function providerTypeLabel(type: string): string {
  return PROVIDER_TYPE_LABELS[type] || type
}

/** 最大输出的来源 → 用户看得懂的说法 */
const MAX_TOKENS_ORIGIN_LABELS: Record<string, string> = {
  连接预设: '模型服务设置',
  'provider 模型': '这个模型的设置',
  'agent 配置': 'AI 对话面板的设置',
  内置默认: '系统默认',
}

function maxTokensOriginLabel(origin?: string): string {
  return MAX_TOKENS_ORIGIN_LABELS[origin || ''] || '系统默认'
}

/** 老预设里可能存着 deepseek / ollama / claude 这类别名：编辑时归一到四种 API 格式 */
function normalizeApiFormat(provider: string): string {
  const raw = (provider || '').trim().toLowerCase()
  if (raw === 'anthropic' || raw === 'claude') return 'anthropic'
  if (raw === 'gemini' || raw === 'google') return 'gemini'
  if (['openai-responses', 'openai_responses', 'openai-response', 'responses'].includes(raw)) {
    return 'openai-responses'
  }
  return 'openai'
}

const API_FORMAT_LABELS: Record<string, string> = {
  anthropic: 'Anthropic Messages',
  openai: 'OpenAI Chat Completions',
  'openai-responses': 'OpenAI Responses',
  gemini: 'Gemini Native',
}

function apiFormatLabel(provider: string): string {
  const format = normalizeApiFormat(provider)
  return API_FORMAT_LABELS[format] || format
}

/** 模型下拉里补上所属服务名，避免两个服务下的同名模型分不清 */
function modelOptionLabel(model: ProviderModel): string {
  const preset = presets.value.find((item) => item.id === model.preset_id)
  return preset ? `${model.model} · ${preset.name}` : model.model
}

const notify = useNotifyStore()
const router = useRouter()
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
const builtinPresets = ref<BuiltinPreset[]>([])
const builtinDialog = ref(false)
const builtinLoading = ref(false)
const importingBuiltinKey = ref('')
// 请求序号：快速切换预设时丢弃过期响应，避免模型表显示别的预设的数据
let modelsSeq = 0

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

const configDialog = ref(false)
const editingConfigPresetId = ref('')
const configJsonText = ref('')
const configError = ref('')
const configSaving = ref(false)
const providerTemplate = ref<any>(null)
/**
 * JSON 编辑器里以只读方式展示的连接参数。
 *
 * 这几个键在「编辑」表单里有校验与更明确的说明；JSON 编辑器只应收尾表单没暴露的高级键
 * （headers / extra_body 等），否则同一份配置有两个入口、且 JSON 路径能绕过校验。
 */
const JSON_READONLY_KEYS = ['api_base', 'api_key', 'retry_attempts', 'timeout'] as const
const readonlyConfigPreview = ref<Record<string, any>>({})

/** 删除确认：服务商/模型的删除都是不可撤销操作，统一走 ConfirmDialog 并列出影响 */
interface PendingDelete {
  kind: 'source' | 'model'
  id: string
  name: string
  details: string[]
}
const pendingDelete = ref<PendingDelete | null>(null)
const deleteLoading = ref(false)
const deleteDialogOpen = computed({
  get: () => pendingDelete.value !== null,
  set: (value: boolean) => {
    if (!value) pendingDelete.value = null
  },
})
const deleteDialogTitle = computed(() =>
  pendingDelete.value?.kind === 'model' ? '删除模型' : '删除模型服务',
)
const deleteDialogMessage = computed(() => {
  const target = pendingDelete.value
  if (!target) return ''
  return target.kind === 'model' ? `模型「${target.name}」将被删除。` : `模型服务「${target.name}」将被删除。`
})

function stripReadonlyConfigKeys(config: Record<string, any> | null | undefined): Record<string, any> {
  const out: Record<string, any> = { ...(config || {}) }
  for (const key of JSON_READONLY_KEYS) delete out[key]
  return out
}

const modelDialog = ref(false)
const editingModelId = ref('')
const modelForm = reactive({
  preset_id: '',
  model: '',
  provider_type: 'chat',
  modalities: ['text', 'tool_use'] as string[],
  temperature: 0.7,
  // null = 不覆盖（回落到连接预设 → Agent 页 → 内置默认）。字段类型必须是
  // `number | null`：写成数字就会在每次保存时把一个"看起来像用户设置"的值烘进去。
  max_tokens: null as number | null,
  context_window: null as number | null,
  reasoning: null as boolean | null,
  enabled: true,
})

const settingsDialog = ref(false)
const settingsSaving = ref(false)
const settingsForm = reactive({
  default_preset_id: '',
  default_model_id: '',
  /** 允许清单；空数组 = 全部可用（后端视为 *） */
  provider_pool: [] as string[],
})

const selectedPreset = computed(() => presets.value.find((p) => p.id === selectedPresetId.value))

/** 内置预设按 category 分组，保持后端目录里的先后顺序 */
const builtinCategories = computed(() => {
  const groups: { title: string; items: BuiltinPreset[] }[] = []
  for (const item of builtinPresets.value) {
    const title = item.category || '其它'
    let group = groups.find((g) => g.title === title)
    if (!group) {
      group = { title, items: [] }
      groups.push(group)
    }
    group.items.push(item)
  }
  return groups
})

function defaultModalitiesFor(type: string): string[] {
  if (type === 'tts' || type === 'stt') return ['audio']
  if (type === 'embedding' || type === 'rerank') return ['text']
  return ['text', 'tool_use']
}

function modelModalityLabels(model: ProviderModel): string[] {
  const list = model.config?.modalities
  if (!Array.isArray(list)) return ['文本']
  return list.map((item: string) => MODALITY_LABELS[item] || item)
}

/** 生效值的来源配色：模型自己设的（蓝）vs 继承来的（灰） */
function maxTokensOriginColor(model: ProviderModel): string {
  const origin = model.effective?.max_tokens_origin || ''
  return origin.includes('provider 模型') ? 'primary' : 'medium-emphasis'
}

const maxTokensHint = computed(() => {
  const window = Number(modelForm.context_window) || 0
  if (modelForm.max_tokens == null) {
    return window
      ? `留空 = 自动（当前上下文窗口 ${window}，按窗口的 1/4 估算）`
      : '留空 = 自动（用模型服务或全局设置里的值）'
  }
  if (window && Number(modelForm.max_tokens) >= window) {
    return `不能大于等于上下文窗口（${window}）—— 输出也算在窗口里`
  }
  return ''
})

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
  const seq = ++modelsSeq
  loadingModels.value = true
  try {
    const res = await http.get<{ ok: boolean; models: ProviderModel[] }>('/api/provider-models', {
      params: { preset_id: selectedPresetId.value },
    })
    if (seq !== modelsSeq) return
    models.value = res.data?.models || []
  } catch (err) {
    if (seq !== modelsSeq) return
    notify.push(errorMessage(err), 'error')
  } finally {
    if (seq === modelsSeq) loadingModels.value = false
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
    // provider_pool 支持 "*"（全允许）；UI 用空数组表达"全部"，不再让用户手抄内部 id
    const pool = Array.isArray(s.provider_pool)
      ? s.provider_pool.map((item: string) => String(item)).filter((item: string) => item && item !== '*')
      : []
    Object.assign(settingsForm, {
      default_preset_id: s.default_preset_id || '',
      default_model_id: s.default_model_id || '',
      provider_pool: pool,
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
      // 全局回退链已由「AI 对话面板 → 模型」按账号维护，这里不再提供手填入口
      fallback_model_ids: [],
      provider_pool: settingsForm.provider_pool,
    })
    notify.push('模型服务设置已保存', 'success')
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
    provider: normalizeApiFormat(preset.provider),
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
    // 保留配置文件里那些这个表单没暴露的键（headers / extra_body / 自定义字段）：
    // 全部重写会把 OpenCode 的 x-opencode-session 头之类整丢掉，保存一次就坏一次。
    const existing = editingSourceId.value
      ? presets.value.find((p) => p.id === editingSourceId.value)
      : undefined
    const config: Record<string, any> = {
      ...(existing?.config || {}),
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
      notify.push('模型服务已更新', 'success')
    } else {
      const res = await http.post<{ preset: ProviderPreset }>('/api/provider-presets', payload)
      selectedPresetId.value = res.data?.preset?.id || ''
      notify.push('模型服务已创建', 'success')
    }
    sourceDialog.value = false
    await loadPresets()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    savingSource.value = false
  }
}

async function loadTemplate() {
  try {
    const res = await http.get<{ ok: boolean; template: any }>('/api/provider-presets/template')
    providerTemplate.value = res.data?.template || null
  } catch {
    providerTemplate.value = null
  }
}

async function loadBuiltins() {
  builtinLoading.value = true
  try {
    const res = await http.get<{ ok: boolean; presets: BuiltinPreset[] }>('/api/provider-presets/builtin')
    builtinPresets.value = res.data?.presets || []
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    builtinLoading.value = false
  }
}

function openBuiltinDialog() {
  loadBuiltins()
  builtinDialog.value = true
}

async function importBuiltin(item: BuiltinPreset) {
  if (importingBuiltinKey.value) return
  importingBuiltinKey.value = item.key
  try {
    const res = await http.post<{ ok: boolean; created: boolean; preset: ProviderPreset }>(
      `/api/provider-presets/builtin/${item.key}/import`,
      {},
    )
    const created = res.data?.created
    notify.push(created ? `已添加「${item.name}」，粘贴 API Key 后保存` : `「${item.name}」已存在`, created ? 'success' : 'info')
    if (res.data?.preset?.id) selectedPresetId.value = res.data.preset.id
    await loadPresets()
    if (created && res.data?.preset) {
      // 新加的预设还没有 Key：直接弹编辑框，省一次点击
      builtinDialog.value = false
      openEditSource(res.data.preset)
    } else {
      await loadBuiltins()
    }
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    importingBuiltinKey.value = ''
  }
}

function openConfigEditor(preset: ProviderPreset) {
  editingConfigPresetId.value = preset.id
  readonlyConfigPreview.value = { ...(preset.config || {}) }
  configJsonText.value = JSON.stringify(stripReadonlyConfigKeys(preset.config), null, 2)
  configError.value = ''
  configDialog.value = true
}

function resetToTemplate() {
  if (!providerTemplate.value) return
  readonlyConfigPreview.value = { ...(providerTemplate.value.config || {}) }
  configJsonText.value = JSON.stringify(stripReadonlyConfigKeys(providerTemplate.value.config), null, 2)
  configError.value = ''
}

async function saveConfigJson() {
  let editable: Record<string, any>
  try {
    editable = JSON.parse(configJsonText.value)
  } catch {
    configError.value = 'JSON 格式不正确'
    return
  }
  if (!editable || typeof editable !== 'object' || Array.isArray(editable)) {
    configError.value = '配置必须是 JSON 对象'
    return
  }
  if (editable.headers !== undefined && (typeof editable.headers !== 'object' || editable.headers === null || Array.isArray(editable.headers))) {
    configError.value = '请求头（headers）必须是对象'
    return
  }
  if (editable.extra_body !== undefined && (typeof editable.extra_body !== 'object' || editable.extra_body === null || Array.isArray(editable.extra_body))) {
    configError.value = '请求体扩展（extra_body）必须是对象'
    return
  }
  // 连接参数不在 JSON 编辑器里改：保存时用现有值原样合并回去（避免被手写覆盖/丢失）
  const source =
    presets.value.find((item) => item.id === editingConfigPresetId.value)?.config || readonlyConfigPreview.value
  const config = stripReadonlyConfigKeys(editable)
  for (const key of JSON_READONLY_KEYS) {
    if (source[key] !== undefined) config[key] = source[key]
  }
  if (!String(config.api_base || '').trim()) {
    configError.value = '接口地址为空，请用「编辑」填写接口地址'
    return
  }
  configSaving.value = true
  try {
    await http.put(`/api/provider-presets/${editingConfigPresetId.value}`, { config })
    notify.push('配置已保存', 'success')
    configDialog.value = false
    await loadPresets()
  } catch (err) {
    configError.value = errorMessage(err)
  } finally {
    configSaving.value = false
  }
}

async function askDeleteSource(preset: ProviderPreset) {
  let modelCount = 0
  let defaultsInUse = false
  try {
    const res = await http.get<{ models: ProviderModel[] }>('/api/provider-models', {
      params: { preset_id: preset.id },
    })
    modelCount = (res.data?.models || []).length
    defaultsInUse = (res.data?.models || []).some((model) => model.id === settingsForm.default_model_id)
  } catch {
    modelCount = 0
  }
  pendingDelete.value = {
    kind: 'source',
    id: preset.id,
    name: preset.name,
    details: [
      `它下面的 ${modelCount} 个模型会一起删除`,
      defaultsInUse ? '其中包含全局默认模型，删除后会回落到其它模型' : '正在使用这些模型的账号会回落到默认模型',
      '删除后不能撤销',
    ],
  }
}

function askDeleteModel(model: ProviderModel) {
  pendingDelete.value = {
    kind: 'model',
    id: model.id,
    name: model.model,
    details: [
      '这个模型的单独设置会一起删除',
      model.id === settingsForm.default_model_id
        ? '它是全局默认模型，删除后没选模型的账号会没有可用模型'
        : '正在使用它的账号会回落到默认模型',
      '删除后不能撤销',
    ],
  }
}

async function confirmDelete() {
  const target = pendingDelete.value
  if (!target || deleteLoading.value) return
  deleteLoading.value = true
  try {
    if (target.kind === 'source') {
      await http.delete(`/api/provider-presets/${target.id}`)
      notify.push('模型服务已删除', 'success')
      if (selectedPresetId.value === target.id) selectedPresetId.value = ''
      await loadPresets()
    } else {
      await http.delete(`/api/provider-models/${target.id}`)
      notify.push('模型已删除', 'success')
      await loadModels()
    }
    pendingDelete.value = null
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    deleteLoading.value = false
  }
}

function openCreateModel() {
  if (!selectedPresetId.value) return
  editingModelId.value = ''
  Object.assign(modelForm, {
    preset_id: selectedPresetId.value,
    model: '',
    provider_type: 'chat',
    modalities: defaultModalitiesFor('chat'),
    temperature: 0.7,
    // 留空 = 不覆盖（回落到连接预设 → Agent 页 → 内置默认）
    max_tokens: null,
    context_window: null,
    reasoning: null,
    enabled: true,
  })
  modelDialog.value = true
}

function openEditModel(model: ProviderModel) {
  editingModelId.value = model.id
  const savedModalities = Array.isArray(model.config?.modalities)
    ? model.config.modalities
    : defaultModalitiesFor(model.provider_type)
  Object.assign(modelForm, {
    preset_id: model.preset_id,
    model: model.model,
    provider_type: model.provider_type,
    modalities: [...savedModalities],
    temperature: model.config.temperature ?? 0.7,
    // `?? 1024` 会把"没设"变成一个"看起来像用户设的"值再存回去 —— 那正是
    // Agent 页「最大输出 Tokens」被杀死的路径。这里必须保持 null 原样。
    max_tokens: model.config.max_tokens ?? null,
    context_window: model.config.context_window ?? null,
    reasoning: typeof model.config.reasoning === 'boolean' ? model.config.reasoning : null,
    enabled: model.enabled,
  })
  modelDialog.value = true
}

function resetMaxTokensToInherit() {
  modelForm.max_tokens = null
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
      modalities: [...modelForm.modalities],
      temperature: Number(modelForm.temperature) || 0.7,
      // 空 / null → 不覆盖；后端按三态处理（0 与空串同样视为"清除覆盖"）。
      // 用 `?? null` 而不是 `Number(...) || 1024`：后者会把"没设"变成一个写死的值。
      max_tokens: modelForm.max_tokens == null ? null : Number(modelForm.max_tokens),
      context_window: modelForm.context_window == null ? null : Number(modelForm.context_window),
      reasoning: modelForm.reasoning,
      enabled: modelForm.enabled,
    }
    if (editingModelId.value) {
      await http.put(`/api/provider-models/${editingModelId.value}`, payload)
      notify.push('模型已更新', 'success')
    } else {
      await http.post('/api/provider-models', payload)
      notify.push('模型已添加', 'success')
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

onMounted(() => {
  loadPresets()
  loadTemplate()
  loadSettings()
})
</script>

<template>
  <div>
    <div class="app-page-header" style="align-items: center">
      <div>
        <h1 class="app-page-title">模型服务</h1>
        <div class="app-page-subtitle">在这里填一次模型服务的 Key，所有账号共用</div>
      </div>
      <v-spacer />
      <v-btn variant="tonal" prepend-icon="mdi-tune" class="mr-1" @click="openSettings">全局设置</v-btn>
      <v-btn variant="tonal" prepend-icon="mdi-library-plus" class="mr-1" @click="openBuiltinDialog">添加常用服务</v-btn>
      <v-btn color="primary" variant="tonal" prepend-icon="mdi-plus" @click="openCreateSource">新建模型服务</v-btn>
    </div>

    <v-row class="mt-2">
      <v-col cols="12" md="4" lg="3">
        <v-card variant="outlined">
          <v-card-title class="d-flex align-center">
            <v-icon icon="mdi-api" class="mr-2" color="primary" /> 已添加的模型服务
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
                  <v-btn size="x-small" variant="text" icon="mdi-delete" color="error" @click.stop="askDeleteSource(p)" />
                </template>
              </v-list-item>
            </v-list>
            <div v-if="!presets.length" class="text-center pa-2">
              <div class="text-caption mb-2">还没有模型服务</div>
              <v-btn size="small" color="primary" variant="tonal" prepend-icon="mdi-plus" block @click="openCreateSource">
                新建模型服务
              </v-btn>
            </div>
          </v-card-text>
        </v-card>
      </v-col>

      <v-col cols="12" md="8" lg="9">
        <template v-if="selectedPreset">
          <v-card variant="outlined" class="mb-4">
            <v-card-title class="d-flex align-center">
              <v-icon icon="mdi-server" class="mr-2" color="primary" /> {{ selectedPreset.name }}
              <v-chip size="small" variant="tonal" class="ml-2">{{ apiFormatLabel(selectedPreset.provider) }}</v-chip>
              <v-spacer />
              <v-btn size="small" variant="tonal" prepend-icon="mdi-pencil" @click="openEditSource(selectedPreset)">编辑</v-btn>
              <v-btn size="small" variant="tonal" prepend-icon="mdi-file-code-outline" class="ml-1" @click="openConfigEditor(selectedPreset)">高级配置</v-btn>
              <v-btn size="small" variant="tonal" prepend-icon="mdi-test-tube" class="ml-1" :loading="testingId === `preset:${selectedPreset.id}`" @click="testPreset(selectedPreset)">测试连接</v-btn>
            </v-card-title>
            <v-card-text class="text-caption">
              接口地址：{{ selectedPreset.config.api_base || '—' }} · 重试：{{ selectedPreset.config.retry_attempts || 3 }} · 超时：{{ selectedPreset.config.timeout || 30 }}s
            </v-card-text>
          </v-card>

          <v-card variant="outlined">
            <v-card-title class="d-flex align-center">
              <v-icon icon="mdi-creation" class="mr-2" color="primary" /> 模型
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
                    <th>支持能力</th>
                    <th>温度</th>
                    <th>最大输出</th>
                    <th>状态</th>
                    <th class="text-right">操作</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-if="models.length === 0">
                    <td colspan="7" class="text-center text-caption pa-6">还没有模型，可点「拉取模型」自动获取，或手动添加</td>
                  </tr>
                  <tr v-for="m in models" :key="m.id">
                    <td class="font-weight-medium">{{ m.model }}</td>
                    <td><v-chip size="x-small" variant="tonal">{{ providerTypeLabel(m.provider_type) }}</v-chip></td>
                    <td>
                      <v-chip
                        v-for="cap in modelModalityLabels(m)"
                        :key="cap"
                        size="x-small"
                        variant="tonal"
                        class="mr-1"
                      >{{ cap }}</v-chip>
                    </td>
                    <td class="text-caption">{{ m.config.temperature ?? 0.7 }}</td>
                    <td class="text-caption">
                      {{ m.effective?.max_tokens ?? '—' }}
                      <v-tooltip location="top" max-width="320">
                        <template #activator="{ props }">
                          <v-icon
                            v-bind="props"
                            size="x-small"
                            :color="maxTokensOriginColor(m)"
                            class="ml-1"
                          >mdi-information-outline</v-icon>
                        </template>
                        <div>来源：{{ maxTokensOriginLabel(m.effective?.max_tokens_origin) }}</div>
                        <div v-if="m.config.max_tokens == null">
                          这个模型没有单独设上限，自动沿用别处的值
                        </div>
                        <div v-for="note in m.effective?.max_tokens_notes || []" :key="note">{{ note }}</div>
                        <div v-if="m.effective?.context_window">上下文窗口：{{ m.effective.context_window }}</div>
                        <div v-if="m.effective?.reasoning">思考模型（输出预算有下限）</div>
                      </v-tooltip>
                    </td>
                    <td>
                      <v-chip size="x-small" :color="m.enabled ? 'success' : 'default'" variant="tonal">
                        {{ m.enabled ? '启用' : '停用' }}
                      </v-chip>
                    </td>
                    <td class="text-right">
                      <v-btn size="x-small" variant="text" color="primary" :loading="testingId === m.id" @click="testModel(m)">测试</v-btn>
                      <v-btn size="x-small" variant="text" color="primary" @click="openEditModel(m)">编辑</v-btn>
                      <v-btn size="x-small" variant="text" color="error" @click="askDeleteModel(m)">删除</v-btn>
                    </td>
                  </tr>
                </tbody>
              </v-table>
            </v-card-text>
          </v-card>
        </template>

        <v-card v-else variant="outlined" class="pa-8 text-center text-caption">
          用右上角「新建模型服务」或「添加常用服务」添加，添加后会出现在左侧列表
        </v-card>
      </v-col>
    </v-row>

    <!-- 自定义预设编辑 -->
    <v-dialog v-model="sourceDialog" max-width="520">
      <v-card>
        <v-card-title class="d-flex align-center">
          <v-icon icon="mdi-api" class="mr-2" color="primary" />
          {{ editingSourceId ? '编辑模型服务' : '新建模型服务' }}
        </v-card-title>
        <v-card-text>
          <v-text-field v-model="sourceForm.name" label="名称" placeholder="例如：DeepSeek 官方" density="comfortable" hide-details class="mb-3" />
          <v-select
            v-model="sourceForm.provider"
            :items="API_FORMAT_OPTIONS"
            item-title="title"
            item-value="value"
            item-props
            label="接口格式"
            density="comfortable"
            hide-details
            class="mb-2"
          />
          <div class="text-caption text-medium-emphasis mb-3">
            不确定选哪个就用「OpenAI Chat Completions」；保存后可以点「测试连接」验证。
          </div>
          <v-text-field v-model="sourceForm.api_base" label="接口地址" placeholder="https://api.deepseek.com" density="comfortable" hide-details class="mb-3" />
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

    <!-- 添加内置预设（端点清单抄自 DSH provider catalog） -->
    <v-dialog v-model="builtinDialog" max-width="760" scrollable>
      <v-card>
        <v-card-title class="d-flex align-center">
          <v-icon icon="mdi-library-plus" class="mr-2" color="primary" /> 添加常用服务
          <v-spacer />
          <v-btn size="small" variant="text" prepend-icon="mdi-refresh" :loading="builtinLoading" @click="loadBuiltins">刷新</v-btn>
        </v-card-title>
        <v-card-subtitle class="text-caption">
          常用的模型服务，点「添加」即可；Key 和模型随后填。
        </v-card-subtitle>
        <v-divider />
        <v-card-text>
          <v-progress-linear v-if="builtinLoading && !builtinPresets.length" indeterminate color="primary" />
          <template v-for="group in builtinCategories" :key="group.title">
            <div class="text-subtitle-2 mt-3 mb-1">{{ group.title }}</div>
            <v-list density="compact">
              <v-list-item v-for="item in group.items" :key="item.key">
                <template #prepend>
                  <v-icon icon="mdi-server-network" color="primary" />
                </template>
                <v-list-item-title>{{ item.name }}</v-list-item-title>
                <v-list-item-subtitle>
                  <code class="text-caption">{{ item.api_base }}</code>
                </v-list-item-subtitle>
                <template #append>
                  <v-chip
                    v-if="item.installed_preset_id"
                    size="small"
                    color="success"
                    variant="tonal"
                    prepend-icon="mdi-check"
                  >已添加</v-chip>
                  <v-btn
                    v-else
                    size="small"
                    color="primary"
                    variant="tonal"
                    prepend-icon="mdi-plus"
                    :loading="importingBuiltinKey === item.key"
                    @click="importBuiltin(item)"
                  >添加</v-btn>
                </template>
              </v-list-item>
            </v-list>
            <v-divider class="my-1" />
          </template>
          <div v-if="!builtinLoading && !builtinPresets.length" class="text-caption text-center py-6">暂无常用服务</div>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="builtinDialog = false">关闭</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 配置文件编辑 -->
    <v-dialog v-model="configDialog" max-width="760">
      <v-card>
        <v-card-title class="d-flex align-center">
          <v-icon icon="mdi-file-code-outline" class="mr-2" color="primary" /> 高级配置
          <v-spacer />
          <v-btn size="small" variant="tonal" prepend-icon="mdi-restore" :disabled="!providerTemplate" @click="resetToTemplate">
            重置为基础模板
          </v-btn>
        </v-card-title>
        <v-card-text>
          <v-alert type="info" variant="tonal" density="compact" class="mb-2">
            只有服务商明确要求补充特殊参数时才需要改这里；不确定就保持原样。
          </v-alert>
          <div class="config-readonly mb-2">
            <div class="text-caption mb-1">连接参数请用「编辑」修改；这里只改请求头、请求体扩展等高级键。</div>
            <div class="text-caption">接口地址：{{ readonlyConfigPreview.api_base || '未设置' }}</div>
            <div class="text-caption">API Key：{{ readonlyConfigPreview.api_key ? '已填写（不在编辑框里显示）' : '未填写' }}</div>
            <div class="text-caption">
              重试次数：{{ readonlyConfigPreview.retry_attempts ?? 3 }} · 超时：{{ readonlyConfigPreview.timeout ?? 30 }} 秒
            </div>
          </div>
          <v-textarea
            v-model="configJsonText"
            rows="20"
            spellcheck="false"
            class="provider-config-editor"
          />
          <v-alert v-if="configError" type="error" variant="tonal" density="compact" class="mt-2">
            {{ configError }}
          </v-alert>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="configDialog = false">取消</v-btn>
          <v-btn color="primary" variant="tonal" :loading="configSaving" @click="saveConfigJson">保存配置</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 模型编辑 -->
    <v-dialog v-model="modelDialog" max-width="480">
      <v-card>
        <v-card-title class="d-flex align-center">
          <v-icon icon="mdi-creation" class="mr-2" color="primary" />
          {{ editingModelId ? '编辑模型' : '添加模型' }}
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
              { title: '对话', value: 'chat' },
              { title: '文本向量（Embedding）', value: 'embedding' },
              { title: '重排（Rerank）', value: 'rerank' },
              { title: '语音合成（TTS）', value: 'tts' },
              { title: '语音识别（STT）', value: 'stt' },
            ]"
            label="能力类型"
            density="comfortable"
            hide-details
            class="mb-3"
          />
          <div class="mb-3">
            <div class="text-caption mb-1">支持能力</div>
            <div class="d-flex flex-wrap">
              <v-checkbox
                v-for="opt in MODALITY_OPTIONS"
                :key="opt.value"
                v-model="modelForm.modalities"
                :value="opt.value"
                :label="opt.title"
                density="compact"
                hide-details
                color="primary"
                class="mr-4"
              />
            </div>
          </div>
          <v-row>
            <v-col cols="6">
              <v-text-field v-model.number="modelForm.temperature" label="温度" type="number" step="0.1" min="0" max="2" density="comfortable" hide-details />
            </v-col>
            <v-col cols="6">
              <v-text-field
                v-model.number="modelForm.max_tokens"
                label="最大输出 Token"
                type="number"
                min="1"
                max="1000000"
                density="comfortable"
                hide-details
                clearable
                placeholder="留空 = 自动"
                :hint="maxTokensHint"
                persistent-hint
              >
                <template #append-inner>
                  <v-btn
                    v-if="modelForm.max_tokens != null"
                    size="x-small"
                    variant="text"
                    @click="resetMaxTokensToInherit"
                  >改回自动</v-btn>
                </template>
              </v-text-field>
            </v-col>
          </v-row>
          <v-row>
            <v-col cols="6">
              <v-text-field
                v-model.number="modelForm.context_window"
                label="上下文窗口（可选）"
                type="number"
                min="1"
                density="comfortable"
                hide-details
                clearable
                placeholder="留空 = 不限制"
                hint="填了才能提前发现内容超出模型上限，不用等服务商报错"
                persistent-hint
              />
            </v-col>
            <v-col cols="6">
              <v-select
                v-model="modelForm.reasoning"
                :items="[
                  { title: '自动（按模型名判断）', value: null },
                  { title: '是，思考模型', value: true },
                  { title: '否，普通模型', value: false },
                ]"
                label="思考模型"
                density="comfortable"
                hide-details
                hint="思考模型会先花预算思考，输出上限有下限"
                persistent-hint
              />
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
          <v-icon icon="mdi-tune" class="mr-2" color="primary" /> 模型服务全局设置
        </v-card-title>
        <v-card-text class="text-caption pt-0 pb-0" style="opacity: 0.7">
          没在 AI 对话面板选模型的账号，用这里的默认模型。
        </v-card-text>
        <v-card-text>
          <v-select
            v-model="settingsForm.default_preset_id"
            :items="presets.map((p) => ({ title: p.name, value: p.id }))"
            label="默认模型服务"
            density="comfortable"
            hide-details
            clearable
            class="mb-3"
          />
          <v-select
            v-model="settingsForm.default_model_id"
            :items="allModels.map((m) => ({ title: modelOptionLabel(m), value: m.id }))"
            label="默认模型"
            density="comfortable"
            hide-details
            clearable
            class="mb-3"
          />
          <v-autocomplete
            v-model="settingsForm.provider_pool"
            :items="allModels.map((m) => ({ title: modelOptionLabel(m), value: m.id }))"
            label="限制可用的模型"
            hint="留空 = 全部可用"
            multiple
            chips
            closable-chips
            clearable
            density="comfortable"
            hide-details="auto"
            persistent-hint
          />
        </v-card-text>
        <v-card-actions>
          <v-btn variant="text" prepend-icon="mdi-open-in-new" @click="settingsDialog = false; router.push('/ai')">
            去 AI 对话面板按账号选模型
          </v-btn>
          <v-spacer />
          <v-btn variant="text" @click="settingsDialog = false">取消</v-btn>
          <v-btn color="primary" variant="tonal" :loading="settingsSaving" @click="saveSettings">保存</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 删除确认：列出影响清单，避免一句话 confirm 让人误删 -->
    <ConfirmDialog
      v-model="deleteDialogOpen"
      :title="deleteDialogTitle"
      :message="deleteDialogMessage"
      :details="pendingDelete?.details || []"
      danger
      confirm-text="删除"
      :loading="deleteLoading"
      @confirm="confirmDelete"
    />
  </div>
</template>

<style scoped>
.provider-config-editor :deep(textarea) {
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 13px;
  line-height: 1.6;
}

.config-readonly {
  padding: 10px 12px;
  border: 1px dashed rgba(var(--v-theme-on-surface), 0.18);
  border-radius: 8px;
  background: rgba(var(--v-theme-on-surface), 0.02);
}
</style>