<script setup lang="ts">
/**
 * 首启向导（4 步）：连接 → 模型 → 人格 → 完成。
 *
 * 设计约束（docs/oneclick-bundle-and-onboarding-design.md §7.2）：
 * - 每一步都只有一个问题，且**同屏完成**（例如"拉模型"和"入池"不再分两页）；
 * - 每一步都可以直接「下一步」（先跳过、稍后再回来补，总览会给 blocker 提示）；
 * - 第 4 步的清单与总览就绪卡、启动横幅同源（`/api/readiness`）。
 */
import { computed, onBeforeUnmount, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import http, { errorMessage } from '@/api/http'
import { useBotsStore } from '@/stores/bots'
import { useBundlesStore } from '@/stores/bundles'
import { useReadinessStore } from '@/stores/readiness'
import { useNotifyStore } from '@/stores/notify'
import BundleImportDialog from '@/components/bundles/BundleImportDialog.vue'

const router = useRouter()
const bots = useBotsStore()
const bundles = useBundlesStore()
const readiness = useReadinessStore()
const notify = useNotifyStore()

const step = ref(1)
const STEPS = ['连接账号', '配置模型', '人格设定', '完成']
const busy = ref('')

// ---------- 第 1 步：连接（向导只维护一个默认账号） ----------
// 默认监听地址由后端 /api/setup/default-account 统一给出（与账号管理同源）；
// 向外连接（正向）不预填，等用户填协议端地址。
const defaultListenAddress = ref('')
const account = reactive({
  id: '',
  name: '',
  conn_mode: 'reverse' as 'reverse' | 'forward',
  ws_url: '',
  access_token: '',
})
// 后台每 2s 刷新一次「是否已连接」，前端不显示重试次数之类的过程状态
const accountConnected = ref(false)
const accountSaved = ref(false)
/** 字段有改动还没保存：轮询不能拿服务端旧值覆盖用户正在输入的内容 */
const accountDirty = ref(false)
/** 保存失败的原因：显示在字段下方，同一条只弹一次提示 */
const accountError = ref('')
/** 保存进行中：只用于按钮转圈，不做互斥（同一账号重复保存是幂等的） */
const savingAccount = ref(false)
let lastNotifiedError = ''
let pollTimer: number | null = null

// ---------- 第 2 步：模型 ----------
// 内置目录优先读后端（与「模型服务」页同源），取不到时用这份本地兜底，保证向导可继续。
interface PresetOption {
  key: string
  name: string
  provider: string
  api_base: string
  models: string[]
}

const FALLBACK_PRESETS: PresetOption[] = [
  { key: 'deepseek', name: 'DeepSeek', provider: 'openai', api_base: 'https://api.deepseek.com', models: ['deepseek-flash', 'deepseek-v4-pro'] },
  { key: 'openai', name: 'OpenAI', provider: 'openai', api_base: 'https://api.openai.com/v1', models: ['gpt-5.2', 'gpt-5.4-mini', 'gpt-4.1'] },
  { key: 'moonshotai-cn', name: 'Kimi（月之暗面）', provider: 'openai', api_base: 'https://api.moonshot.cn/v1', models: ['kimi-k3', 'kimi-k2.7-code', 'kimi-k2.6'] },
  { key: 'zai-coding-cn', name: '智谱 GLM', provider: 'openai', api_base: 'https://open.bigmodel.cn/api/coding/paas/v4', models: ['glm-5.3', 'glm-4.6v'] },
  { key: 'qwen-token-plan-cn', name: '通义千问', provider: 'openai', api_base: 'https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1', models: ['qwen3.8-max', 'qwen3.8-flash'] },
  { key: 'anthropic', name: 'Anthropic', provider: 'anthropic', api_base: 'https://api.anthropic.com', models: ['claude-sonnet-5', 'claude-haiku-5-5'] },
]

/** 向导首屏优先展示的服务商（其余收进「其它服务」） */
const RECOMMENDED_KEYS = ['deepseek', 'moonshotai-cn', 'zai-coding-cn', 'qwen-token-plan-cn', 'openai']

const presets = ref<PresetOption[]>([])
const presetsLoading = ref(false)
const presetsError = ref('')
const selectedPresetKey = ref('')
const customMode = ref(false)
const moreOpen = ref(false)

const model = reactive({
  provider: 'openai',
  api_base: '',
  api_key: '',
  model: '',
  preset_name: '',
})
const modelResult = ref<{ model_id: string; preset_name: string } | null>(null)
const modelTesting = ref(false)
const modelTestMessage = ref('')

const selectedModelPreset = computed(
  () => presets.value.find((item) => item.key === selectedPresetKey.value) || null,
)
const recommendedPresets = computed(() =>
  presets.value.filter((item) => RECOMMENDED_KEYS.includes(item.key)),
)
const morePresets = computed(() =>
  presets.value.filter((item) => !RECOMMENDED_KEYS.includes(item.key)),
)
const customProviderItems = [
  { title: 'OpenAI 兼容', value: 'openai' },
  { title: 'Anthropic', value: 'anthropic' },
  { title: 'Gemini', value: 'gemini' },
]

/** 选中一家服务商：自动填好接口地址与默认模型，用户只需再填 Key */
function choosePreset(preset: PresetOption) {
  customMode.value = false
  selectedPresetKey.value = preset.key
  model.provider = preset.provider
  model.api_base = preset.api_base
  model.model = preset.models[0] || ''
  model.preset_name = preset.name
}

/** 自定义：手填接口地址与模型名（自建服务、代理等） */
function chooseCustom() {
  customMode.value = true
  selectedPresetKey.value = ''
  model.preset_name = ''
}

async function loadPresets() {
  presetsLoading.value = true
  try {
    const res = await http.get('/api/provider-presets/builtin')
    const rows: unknown[] = Array.isArray(res.data?.presets) ? res.data.presets : []
    const parsed: PresetOption[] = rows
      .map((row) => row as Record<string, unknown>)
      .map((row) => ({
        key: String(row.key || row.name || ''),
        name: String(row.name || ''),
        provider: String(row.provider || 'openai'),
        api_base: String(row.api_base || ''),
        models: Array.isArray(row.models) ? row.models.map(String) : [],
      }))
      .filter((item) => item.key && item.api_base)
    presets.value = parsed.length ? parsed : FALLBACK_PRESETS
    presetsError.value = parsed.length ? '' : '没读到模型服务列表，先用内置的几家'
  } catch {
    presets.value = FALLBACK_PRESETS
    presetsError.value = '没读到模型服务列表，先用内置的几家'
  } finally {
    presetsLoading.value = false
  }
  if (!selectedPresetKey.value && !customMode.value && presets.value.length) {
    choosePreset(presets.value.find((item) => item.key === 'deepseek') || presets.value[0])
  }
}

// ---------- 第 3 步：人格 ----------
const personaMode = ref<'scratch' | 'bundle'>('scratch')
const persona = reactive({ name: '群聊搭子', prompt: '' })
const personaDone = ref(false)

// 选预设：导入 + 应用到当前账号（会带上人格/模型池/Agent 配置/随包模块）
const importDialog = ref(false)
const selectedPresetId = ref('')
const presetSecrets = reactive<Record<string, string>>({})
const presetApplying = ref(false)
const appliedPreset = ref<{ name: string; version: string } | null>(null)

const selectedPreset = computed(
  () => bundles.list.find((item) => item.id === selectedPresetId.value) || null,
)

function selectPreset(id: string) {
  selectedPresetId.value = id
  Object.keys(presetSecrets).forEach((key) => delete presetSecrets[key])
  for (const secret of selectedPreset.value?.secrets || []) presetSecrets[secret.key] = ''
}

async function onPresetImported() {
  await bundles.load()
  const first = bundles.list[0]
  if (!selectedPresetId.value && first) selectPreset(first.id)
}

async function applyPreset() {
  if (!selectedPresetId.value) {
    notify.push('先选一个预设', 'warning')
    return
  }
  if (!currentAccountId.value) {
    notify.push('请先在第一步连接账号', 'warning')
    return
  }
  presetApplying.value = true
  try {
    await bundles.applyBundle(
      selectedPresetId.value,
      currentAccountId.value,
      { ...presetSecrets },
    )
    const target = selectedPreset.value
    appliedPreset.value = target
      ? { name: target.name, version: target.version }
      : { name: selectedPresetId.value, version: '' }
    personaDone.value = true
    await Promise.all([readiness.load(), bundles.loadActivations(currentAccountId.value)])
    notify.push('预设已应用：人格 / 模型 / 模块已就位', 'success')
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    presetApplying.value = false
  }
}

const currentAccountId = computed(() => account.id.trim() || bots.currentAccountId)


async function run(label: string, task: () => Promise<void>) {
  busy.value = label
  try {
    await task()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    busy.value = ''
  }
}

// ---------- 第 1 步动作 ----------
/**
 * 保存向导账号：只在失焦 / 点「下一步」时调用，打字过程中不建号。
 * 失败把原因显示在字段下方，并用提示条告知一次（同一条不重复弹）。
 */
async function saveAccount(): Promise<boolean> {
  savingAccount.value = true
  try {
    const res = await http.post('/api/setup/default-account', {
      id: account.id.trim(),
      name: account.name.trim(),
      ws_url: account.ws_url.trim(),
      access_token: account.access_token,
      conn_mode: account.conn_mode,
    })
    account.id = res.data.account_id
    accountSaved.value = true
    accountDirty.value = false
    accountError.value = ''
    lastNotifiedError = ''
    accountConnected.value = Boolean(res.data.connected)
    // 让顶部账号选择器/后续页面拿到这个账号（否则向导走完仍是"未选账号"）
    bots.selectBot(res.data.account_id)
    await bots.fetchBots()
    return true
  } catch (err) {
    const message = errorMessage(err)
    accountError.value = message
    if (message !== lastNotifiedError) {
      lastNotifiedError = message
      notify.push(message, 'error')
    }
    return false
  } finally {
    savingAccount.value = false
  }
}

/** 输入只更新本地草稿；顺带清掉上一次的错误提示 */
function markAccountDirty() {
  accountDirty.value = true
  accountError.value = ''
}

function onModeChange(mode: 'reverse' | 'forward') {
  account.conn_mode = mode
  account.ws_url = mode === 'reverse' ? defaultListenAddress.value : ''
  markAccountDirty()
}

async function nextFromAccount() {
  // 点「下一步」才真正建号/保存；失败就停在本步
  if (!(await saveAccount())) return
  step.value = 2
}

async function refreshAccount() {
  try {
    const res = await http.get('/api/setup/default-account')
    const current = res.data?.account
    defaultListenAddress.value = String(res.data?.default_address || '')
    accountConnected.value = Boolean(current?.connected)
    if (current && !accountSaved.value && !accountDirty.value) {
      const raw = String(current.ws_url || '')
      const [base, query] = raw.split('?')
      account.id = current.account_id
      account.name = current.name === current.account_id ? '' : current.name
      account.conn_mode = current.conn_mode === 'forward' ? 'forward' : 'reverse'
      account.ws_url = base || (account.conn_mode === 'reverse' ? defaultListenAddress.value : '')
      if (!account.access_token && query?.includes('access_token=')) {
        account.access_token = query.split('access_token=')[1]?.split('&')[0] || ''
      }
      accountSaved.value = true
    } else if (!current && !accountSaved.value && account.conn_mode === 'reverse' && !account.ws_url) {
      // 还没有账号：先把后端给的监听地址预填出来（这时还不建号，点「下一步」才保存）
      account.ws_url = defaultListenAddress.value
    }
  } catch {
    // 刷新失败保留上一次状态，下一轮再试
  }
}

function startPolling() {
  stopPolling()
  pollTimer = window.setInterval(() => void refreshAccount(), 2000)
}

function stopPolling() {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
}

// ---------- 第 2 步动作 ----------
async function saveModel() {
  if (!currentAccountId.value) {
    notify.push('请先在第一步连接账号', 'warning')
    return
  }
  if (!model.api_base.trim()) {
    notify.push('先选一家模型服务，或填好接口地址', 'warning')
    return
  }
  if (!model.model.trim()) {
    notify.push('选一个模型，或直接填模型名', 'warning')
    return
  }
  if (!model.api_key.trim()) {
    notify.push('填上 API Key 才能用', 'warning')
    return
  }
  await run('model', async () => {
    const res = await http.post('/api/setup/quick-model', {
      account_id: currentAccountId.value,
      provider: model.provider,
      api_base: model.api_base.trim(),
      api_key: model.api_key,
      model: model.model.trim(),
      preset_name: model.preset_name,
    })
    modelResult.value = { model_id: res.data.model_id, preset_name: res.data.preset_name }
    notify.push('模型已接入', 'success')
  })
}

async function testModel() {
  if (!modelResult.value) {
    notify.push('先保存模型', 'warning')
    return
  }
  modelTesting.value = true
  modelTestMessage.value = ''
  try {
    const res = await http.post(`/api/provider-models/${modelResult.value.model_id}/test`)
    modelTestMessage.value = res.data?.message || '连通正常'
    notify.push('连通测试通过', 'success')
  } catch (err) {
    modelTestMessage.value = errorMessage(err)
    notify.push(modelTestMessage.value, 'error')
  } finally {
    modelTesting.value = false
  }
}

// ---------- 第 3 步动作 ----------
async function savePersona() {
  if (!currentAccountId.value) {
    notify.push('请先连接账号', 'warning')
    return
  }
  if (!persona.prompt.trim()) {
    notify.push('写一句人设，或点「稍后再配」', 'warning')
    return
  }
  await run('persona', async () => {
    const created = await http.post('/api/role-presets', {
      name: persona.name.trim() || '默认人格',
      system_prompt: persona.prompt.trim(),
    })
    const presetId = created.data?.preset?.id
    if (!presetId) throw new Error('角色预设创建失败')
    await http.post(
      '/api/agent/config',
      { enabled: true, config: { group_enable: true, persona_preset_id: presetId } },
      { params: { account_id: currentAccountId.value } },
    )
    personaDone.value = true
    notify.push('人格已应用，群聊回复已开启', 'success')
  })
}

// ---------- 第 4 步 ----------
async function finish() {
  await run('finish', async () => {
    await http.post('/api/setup/complete')
    router.push('/')
  })
}

function skipAll() {
  router.push('/')
}

onMounted(async () => {
  if (!bots.bots.length) await bots.fetchBots()
  await refreshAccount()
  startPolling()
  await Promise.all([readiness.load(), bundles.load(), loadPresets()])
})

onBeforeUnmount(() => {
  // 离开向导前把第 1 步没保存的改动补存一次，避免切页丢配置
  if (accountDirty.value) void saveAccount()
})
onUnmounted(stopPolling)
</script>

<template>
  <div class="setup-page">
    <div class="setup-shell">
      <div class="d-flex align-center flex-wrap gap-3 mb-2">
        <v-icon icon="mdi-rocket-launch-outline" size="34" color="primary" />
        <h1 class="text-h5 font-weight-bold">首次配置</h1>
        <v-chip size="small" variant="tonal">约 3 分钟</v-chip>
        <v-spacer />
        <v-btn variant="text" @click="skipAll">稍后再配</v-btn>
      </div>

      <div class="step-strip mb-4">
        <template v-for="(name, index) in STEPS" :key="name">
          <div
            class="step-item"
            :class="{ 'step-item--active': step === index + 1, 'step-item--done': step > index + 1 }"
          >
            <span class="step-index">{{ index + 1 }}</span>
            <span>{{ name }}</span>
          </div>
        </template>
      </div>

      <!-- 第 1 步 -->
      <v-card v-if="step === 1" variant="outlined">
        <v-card-title>把 QQ 号接进来</v-card-title>
        <v-card-text>
          <div class="text-body-2 text-medium-emphasis mb-2">先选一种连接方式，两种都行，推荐本地监听。</div>
          <v-btn-toggle
            :model-value="account.conn_mode"
            mandatory
            density="comfortable"
            class="mb-4"
            @update:model-value="(value: any) => onModeChange(value)"
          >
            <v-btn value="reverse" prepend-icon="mdi-lan-connect">本地监听（推荐）</v-btn>
            <v-btn value="forward" prepend-icon="mdi-lan-disconnect">向外连接</v-btn>
          </v-btn-toggle>

          <v-table density="compact" class="mode-table mb-5">
            <thead>
              <tr>
                <th>连接方式</th>
                <th>协议端里选</th>
                <th>地址填什么</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>本地监听（推荐）</td>
                <td>WebSocket 客户端</td>
                <td>填本页给出的本机监听地址</td>
              </tr>
              <tr>
                <td>向外连接</td>
                <td>WebSocket 服务端</td>
                <td>把协议端的 ws 地址填到这里</td>
              </tr>
            </tbody>
          </v-table>

          <v-row dense>
            <v-col cols="12" md="6">
              <v-text-field
                v-model="account.id"
                label="账号标识"
                placeholder="例如：main"
                hint="随便起个名字，用来区分多个号"
                persistent-hint
                :error-messages="accountError"
                density="comfortable"
                variant="outlined"
                @update:model-value="markAccountDirty"
                @blur="saveAccount"
              />
            </v-col>
            <v-col cols="12" md="6">
              <v-text-field
                v-model="account.name"
                label="账号名称"
                placeholder="例如：主号"
                hint="给自己看的名字，可留空"
                persistent-hint
                density="comfortable"
                variant="outlined"
                @update:model-value="markAccountDirty"
                @blur="saveAccount"
              />
            </v-col>
          </v-row>

          <v-text-field
            v-model="account.ws_url"
            :label="account.conn_mode === 'reverse' ? '本机监听地址' : '协议端地址'"
            hint="本地监听时协议端填这个地址；向外连接时填协议端的地址"
            persistent-hint
            density="comfortable"
            variant="outlined"
            @update:model-value="markAccountDirty"
            @blur="saveAccount"
          />
          <v-text-field
            v-model="account.access_token"
            label="连接令牌（可选）"
            hint="与协议端里填的一致，没有就留空"
            persistent-hint
            density="comfortable"
            variant="outlined"
            autocomplete="off"
            @update:model-value="markAccountDirty"
            @blur="saveAccount"
          />

          <div class="d-flex align-center gap-2 mt-1">
            <v-icon
              size="small"
              :icon="accountConnected ? 'mdi-check-circle-outline' : 'mdi-circle-outline'"
              :color="accountConnected ? 'success' : 'grey'"
            />
            <span class="text-body-2">{{ accountConnected ? '已连接' : '未连接' }}</span>
          </div>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn color="primary" :loading="savingAccount" @click="nextFromAccount">下一步</v-btn>
        </v-card-actions>
      </v-card>

      <!-- 第 2 步 -->
      <v-card v-else-if="step === 2" variant="outlined">
        <v-card-title>接入 AI 模型</v-card-title>
        <v-card-text>
          <div class="text-body-2 text-medium-emphasis mb-3">
            选一家模型服务，填上它给你的 Key 就能用；换服务商不用改其它设置。
          </div>

          <div v-if="presetsLoading" class="text-caption mb-3">正在读取模型服务列表…</div>
          <template v-else>
            <div class="preset-grid">
              <v-card
                v-for="item in recommendedPresets"
                :key="item.key"
                variant="outlined"
                hover
                class="preset-card"
                :class="{ 'preset-card--active': !customMode && selectedPresetKey === item.key }"
                @click="choosePreset(item)"
              >
                <div class="preset-card__name">{{ item.name }}</div>
                <div class="preset-card__hint">常用模型 {{ item.models.length }} 个</div>
              </v-card>
              <v-card
                variant="outlined"
                hover
                class="preset-card"
                :class="{ 'preset-card--active': customMode }"
                @click="chooseCustom"
              >
                <div class="preset-card__name">自定义</div>
                <div class="preset-card__hint">手填接口地址和模型名</div>
              </v-card>
            </div>

            <v-btn
              v-if="morePresets.length"
              size="small"
              variant="text"
              class="px-0 mt-1"
              @click="moreOpen = !moreOpen"
            >
              {{ moreOpen ? '收起其它服务' : `其它服务（${morePresets.length}）` }}
            </v-btn>
            <v-expand-transition>
              <div v-if="moreOpen" class="preset-grid mt-2">
                <v-card
                  v-for="item in morePresets"
                  :key="item.key"
                  variant="outlined"
                  hover
                  class="preset-card"
                  :class="{ 'preset-card--active': !customMode && selectedPresetKey === item.key }"
                  @click="choosePreset(item)"
                >
                  <div class="preset-card__name">{{ item.name }}</div>
                  <div class="preset-card__hint">常用模型 {{ item.models.length }} 个</div>
                </v-card>
              </div>
            </v-expand-transition>

            <v-alert v-if="presetsError" type="info" variant="tonal" density="compact" class="mt-3">
              {{ presetsError }}
            </v-alert>

            <v-row v-if="!customMode && selectedModelPreset" dense class="mt-2">
              <v-col cols="12" md="6">
                <v-combobox
                  v-model="model.model"
                  :items="selectedModelPreset.models"
                  label="模型"
                  hint="选一个常用模型，也可以直接填名字"
                  persistent-hint
                  density="comfortable"
                  variant="outlined"
                />
              </v-col>
              <v-col cols="12" md="6">
                <v-text-field
                  v-model="model.api_key"
                  label="API Key"
                  hint="在服务商控制台创建"
                  persistent-hint
                  type="password"
                  density="comfortable"
                  variant="outlined"
                  autocomplete="off"
                />
              </v-col>
            </v-row>
            <div v-if="!customMode && selectedModelPreset" class="text-caption text-medium-emphasis">
              接口地址：{{ model.api_base }}
            </div>

            <template v-else>
              <v-row dense class="mt-2">
                <v-col cols="12" md="4">
                  <v-select
                    v-model="model.provider"
                    :items="customProviderItems"
                    label="模型服务"
                    density="comfortable"
                    variant="outlined"
                  />
                </v-col>
                <v-col cols="12" md="8">
                  <v-text-field
                    v-model="model.api_base"
                    label="接口地址（一般不用改）"
                    density="comfortable"
                    variant="outlined"
                  />
                </v-col>
              </v-row>
              <v-row dense>
                <v-col cols="12" md="6">
                  <v-text-field
                    v-model="model.model"
                    label="模型名称"
                    placeholder="如 deepseek-chat"
                    density="comfortable"
                    variant="outlined"
                  />
                </v-col>
                <v-col cols="12" md="6">
                  <v-text-field
                    v-model="model.api_key"
                    label="API Key"
                    type="password"
                    density="comfortable"
                    variant="outlined"
                    autocomplete="off"
                  />
                </v-col>
              </v-row>
            </template>

            <div v-if="modelResult" class="created-box">
              <div class="text-caption">已接入：{{ modelResult.preset_name }}</div>
              <v-btn
                size="small"
                variant="tonal"
                class="mt-2"
                :loading="modelTesting"
                prepend-icon="mdi-connection"
                @click="testModel"
              >
                测试连通
              </v-btn>
              <div v-if="modelTestMessage" class="text-caption mt-1">{{ modelTestMessage }}</div>
            </div>
          </template>
        </v-card-text>
        <v-card-actions>
          <v-btn variant="text" @click="step = 1">上一步</v-btn>
          <v-spacer />
          <v-btn color="primary" variant="tonal" :loading="busy === 'model'" @click="saveModel">
            保存并使用
          </v-btn>
          <v-btn color="primary" @click="step = 3">下一步</v-btn>
        </v-card-actions>
      </v-card>

      <!-- 第 3 步 -->
      <v-card v-else-if="step === 3" variant="outlined">
        <v-card-title>给它一个人设</v-card-title>
        <v-card-text>
          <v-tabs v-model="personaMode" density="compact" class="mb-3">
            <v-tab value="scratch">从零写</v-tab>
            <v-tab value="bundle">选预设 / 导入预设包</v-tab>
          </v-tabs>

          <template v-if="personaMode === 'scratch'">
            <v-text-field
              v-model="persona.name"
              label="人格名称"
              density="comfortable"
              variant="outlined"
            />
            <v-textarea
              v-model="persona.prompt"
              label="人设提示词"
              placeholder="例如：你是一个混在群里的普通群友，说话简短自然，不端着。"
              rows="5"
              auto-grow
              density="comfortable"
              variant="outlined"
            />
            <div class="text-caption" style="opacity: 0.7">
              保存后会同时开启群聊回复，之后可在「AI 对话设置」里继续调整。
            </div>
          </template>
          <template v-else>
            <div class="text-caption mb-3" style="opacity: 0.7">
              用别人分享的预设：导入 .zip 后一键应用人格、模型和随包功能
              （会以预设自带的模型为准，覆盖刚才选的模型）。
            </div>
            <div class="d-flex align-center flex-wrap gap-2 mb-3">
              <v-btn
                size="small"
                color="primary"
                variant="tonal"
                prepend-icon="mdi-import"
                @click="importDialog = true"
              >
                导入预设包
              </v-btn>
              <v-btn size="small" variant="text" :loading="bundles.loading" @click="bundles.load()">
                刷新列表
              </v-btn>
            </div>

            <v-alert v-if="!bundles.list.length && !bundles.loading" type="info" variant="tonal" density="compact">
              还没有预设：导入一个 .zip 预设包，或先用「从零写」。
            </v-alert>

            <v-radio-group v-else v-model="selectedPresetId" hide-details class="mt-0">
              <v-radio
                v-for="item in bundles.list"
                :key="item.id"
                :value="item.id"
                @click="selectPreset(item.id)"
              >
                <template #label>
                  <div class="d-flex align-center flex-wrap gap-1">
                    <span class="font-weight-medium">{{ item.name }}</span>
                    <v-chip size="x-small" variant="tonal">v{{ item.version }}</v-chip>
                    <v-chip v-if="item.modules?.length" size="x-small" variant="text">
                      含模块 {{ item.modules.length }}
                    </v-chip>
                  </div>
                </template>
              </v-radio>
            </v-radio-group>

            <v-card v-if="selectedPreset" variant="tonal" class="mt-3">
              <v-card-text>
                <v-text-field
                  v-for="secret in selectedPreset.secrets || []"
                  :key="secret.key"
                  v-model="presetSecrets[secret.key]"
                  :label="secret.label"
                  :placeholder="secret.required ? '必填（不随包分发）' : '可选'"
                  type="password"
                  density="comfortable"
                  variant="outlined"
                  autocomplete="off"
                  hide-details
                  class="mb-2"
                />
                <v-btn
                  color="primary"
                  variant="tonal"
                  prepend-icon="mdi-play-circle-outline"
                  :loading="presetApplying"
                  @click="applyPreset"
                >
                  应用此预设到当前账号
                </v-btn>
              </v-card-text>
            </v-card>

            <v-alert v-if="appliedPreset" type="success" variant="tonal" density="compact" class="mt-3">
              已应用「{{ appliedPreset.name }}」：人格、模型、模块都配好了。
            </v-alert>
          </template>
        </v-card-text>
        <v-card-actions>
          <v-btn variant="text" @click="step = 2">上一步</v-btn>
          <v-spacer />
          <v-btn
            v-if="personaMode === 'scratch'"
            color="primary"
            variant="tonal"
            :loading="busy === 'persona'"
            @click="savePersona"
          >
            保存并应用
          </v-btn>
          <v-btn color="primary" @click="step = 4">下一步</v-btn>
        </v-card-actions>
      </v-card>

      <!-- 第 4 步 -->
      <v-card v-else variant="outlined">
        <v-card-title>最后确认</v-card-title>
        <v-card-text>
          <div class="text-body-2 text-medium-emphasis mb-2">下面几项都打勾，就能在群里聊天了。</div>
          <div v-for="check in readiness.checks.filter((c) => c.level !== 'info')" :key="check.id" class="d-flex align-center gap-2 py-1">
            <v-icon
              size="small"
              :icon="check.ok ? 'mdi-check-circle-outline' : 'mdi-close-circle-outline'"
              :color="check.ok ? 'success' : check.level === 'blocker' ? 'error' : 'warning'"
            />
            <span class="text-body-2">{{ check.title }}</span>
            <span v-if="check.detail" class="text-caption" style="opacity: 0.65">{{ check.detail }}</span>
          </div>
          <v-alert v-if="readiness.ready" type="success" variant="tonal" density="compact" class="mt-3">
            已经可以聊天了：去群里 @ 一次机器人试试。连接状态在「总览」里看。
          </v-alert>
          <v-alert v-else type="warning" variant="tonal" density="compact" class="mt-3">
            还有 {{ readiness.blockers.length }} 项没完成。可以返回前面补齐，或先保存、稍后在「总览」里继续。
          </v-alert>
        </v-card-text>
        <v-card-actions>
          <v-btn variant="text" @click="step = 3">上一步</v-btn>
          <v-spacer />
          <v-btn variant="text" :loading="readiness.loading" @click="readiness.load()">重新检查</v-btn>
          <v-btn color="primary" :loading="busy === 'finish'" @click="finish">完成并进入总览</v-btn>
        </v-card-actions>
      </v-card>
    </div>

    <BundleImportDialog v-model="importDialog" @imported="onPresetImported" />
  </div>
</template>

<style scoped>
.setup-page {
  min-height: 100vh;
  display: flex;
  justify-content: center;
  padding: 32px 16px;
}

.setup-shell {
  width: 100%;
  max-width: 860px;
}

.step-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.step-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  padding: 4px 10px;
  border-radius: 999px;
  background: rgba(var(--v-theme-on-surface), 0.05);
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.step-item--active {
  background: rgba(var(--v-theme-primary), 0.12);
  color: rgb(var(--v-theme-primary));
  font-weight: 600;
}

.step-item--done {
  color: rgb(var(--v-theme-success));
}

.step-index {
  width: 18px;
  height: 18px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: rgba(var(--v-theme-on-surface), 0.12);
  font-size: 11px;
}

.created-box {
  margin-top: 12px;
  padding: 10px 12px;
  border-radius: 10px;
  background: rgba(var(--v-theme-primary), 0.06);
}

/* 「协议端怎么选」对照表：小而灰，作为选项说明而不是正文 */
.mode-table {
  border-radius: 10px;
  overflow: hidden;
  background: rgba(var(--v-theme-on-surface), 0.03);
  font-size: 12.5px;
}

.mode-table :deep(th) {
  font-size: 12px;
  font-weight: 600;
  color: rgba(var(--v-theme-on-surface), 0.6);
}

/* 模型服务卡片网格 */
.preset-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 8px;
}

.preset-card {
  padding: 10px 12px;
  border-radius: 12px;
  cursor: pointer;
  transition: border-color .15s, background-color .15s;
}

.preset-card--active {
  border-color: rgb(var(--v-theme-primary)) !important;
  background: rgba(var(--v-theme-primary), .06);
}

.preset-card__name {
  font-size: 13.5px;
  font-weight: 600;
  line-height: 1.4;
}

.preset-card__hint {
  margin-top: 2px;
  font-size: 11.5px;
  color: rgba(var(--v-theme-on-surface), .55);
}

.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  word-break: break-all;
}
</style>
