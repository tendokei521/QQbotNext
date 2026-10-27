<script setup lang="ts">
/**
 * 首启向导（4 步）：连接 → 模型 → 人格 → 完成。
 *
 * 设计约束（docs/oneclick-bundle-and-onboarding-design.md §7.2）：
 * - 每一步都只有一个问题，且**同屏完成**（例如"拉模型"和"入池"不再分两页）；
 * - 每一步都可以直接「下一步」（先跳过、稍后再回来补，总览会给 blocker 提示）；
 * - 第 4 步的清单与总览就绪卡、启动横幅同源（`/api/readiness`）。
 */
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
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
let saveTimer: number | null = null
let pollTimer: number | null = null

// ---------- 第 2 步：模型 ----------
const model = reactive({
  provider: 'openai',
  api_base: 'https://api.deepseek.com/v1',
  api_key: '',
  model: 'deepseek-chat',
})
const modelResult = ref<{ model_id: string; preset_name: string } | null>(null)
const modelTesting = ref(false)
const modelTestMessage = ref('')

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
async function saveAccount(quiet = false) {
  const res = await http.post('/api/setup/default-account', {
    id: account.id.trim(),
    name: account.name.trim(),
    ws_url: account.ws_url.trim(),
    access_token: account.access_token,
    conn_mode: account.conn_mode,
  })
  account.id = res.data.account_id
  accountSaved.value = true
  accountConnected.value = Boolean(res.data.connected)
  // 让顶部账号选择器/后续页面拿到这个账号（否则向导走完仍是"未选账号"）
  bots.selectBot(res.data.account_id)
  await bots.fetchBots()
  if (!quiet) notify.push('已保存，正在后台尝试连接', 'success')
}

function scheduleSave() {
  // 还没填任何内容时不建账号（默认地址不算"用户输入"）
  if (!accountSaved.value && !account.id.trim() && !account.name.trim()) return
  if (saveTimer !== null) window.clearTimeout(saveTimer)
  saveTimer = window.setTimeout(() => {
    void saveAccount(true).catch((err) => notify.push(errorMessage(err), 'error'))
  }, 600)
}

function onModeChange(mode: 'reverse' | 'forward') {
  account.conn_mode = mode
  account.ws_url = mode === 'reverse' ? defaultListenAddress.value : ''
  scheduleSave()
}

async function nextFromAccount() {
  try {
    if (!accountSaved.value) await saveAccount(true)
  } catch (err) {
    notify.push(errorMessage(err), 'error')
    return
  }
  step.value = 2
}

async function refreshAccount() {
  try {
    const res = await http.get('/api/setup/default-account')
    const current = res.data?.account
    defaultListenAddress.value = String(res.data?.default_address || '')
    accountConnected.value = Boolean(current?.connected)
    if (current && !accountSaved.value) {
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
      // 还没有账号：先把后端给的监听地址预填出来（不创建账号，用户填 ID/名称才保存）
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
  await run('model', async () => {
    const res = await http.post('/api/setup/quick-model', {
      account_id: currentAccountId.value,
      provider: model.provider,
      api_base: model.api_base.trim(),
      api_key: model.api_key,
      model: model.model.trim(),
    })
    modelResult.value = { model_id: res.data.model_id, preset_name: res.data.preset_name }
    notify.push('模型已接入 Agent', 'success')
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
  await Promise.all([readiness.load(), bundles.load()])
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
          <v-btn-toggle
            :model-value="account.conn_mode"
            mandatory
            density="comfortable"
            class="mb-3"
            @update:model-value="(value: any) => onModeChange(value)"
          >
            <v-btn value="reverse" prepend-icon="mdi-lan-connect">本地监听（反向 WebSocket）</v-btn>
            <v-btn value="forward" prepend-icon="mdi-lan-disconnect">向外连接（正向 WebSocket）</v-btn>
          </v-btn-toggle>

          <v-row dense>
            <v-col cols="12" md="6">
              <v-text-field
                v-model="account.id"
                label="账号 ID"
                placeholder="例如：default"
                density="comfortable"
                variant="outlined"
                @update:model-value="scheduleSave"
              />
            </v-col>
            <v-col cols="12" md="6">
              <v-text-field
                v-model="account.name"
                label="账号名称"
                placeholder="例如：主号"
                density="comfortable"
                variant="outlined"
                @update:model-value="scheduleSave"
              />
            </v-col>
          </v-row>

          <v-text-field
            v-model="account.ws_url"
            :label="account.conn_mode === 'reverse' ? '监听地址' : '协议端地址'"
            density="comfortable"
            variant="outlined"
            @update:model-value="scheduleSave"
          />
          <v-text-field
            v-model="account.access_token"
            label="Access Token（可选）"
            density="comfortable"
            variant="outlined"
            autocomplete="off"
            @update:model-value="scheduleSave"
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
          <v-btn color="primary" @click="nextFromAccount">下一步</v-btn>
        </v-card-actions>
      </v-card>

      <!-- 第 2 步 -->
      <v-card v-else-if="step === 2" variant="outlined">
        <v-card-title>配置模型（保存即入池）</v-card-title>
        <v-card-text>
          <v-row dense>
            <v-col cols="12" md="4">
              <v-select
                v-model="model.provider"
                :items="[
                  { title: 'OpenAI 兼容', value: 'openai' },
                  { title: 'Anthropic', value: 'anthropic' },
                  { title: 'Gemini', value: 'gemini' },
                ]"
                label="服务商协议"
                density="comfortable"
                variant="outlined"
              />
            </v-col>
            <v-col cols="12" md="8">
              <v-text-field
                v-model="model.api_base"
                label="API 基础 URL"
                density="comfortable"
                variant="outlined"
              />
            </v-col>
          </v-row>
          <v-text-field
            v-model="model.api_key"
            label="API Key"
            type="password"
            density="comfortable"
            variant="outlined"
            autocomplete="off"
          />
          <v-text-field
            v-model="model.model"
            label="模型名称"
            placeholder="如 deepseek-chat"
            density="comfortable"
            variant="outlined"
          />
          <div v-if="modelResult" class="created-box">
            <div class="text-caption">
              已接入：{{ modelResult.preset_name }} · {{ modelResult.model_id }}
            </div>
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
        </v-card-text>
        <v-card-actions>
          <v-btn variant="text" @click="step = 1">上一步</v-btn>
          <v-spacer />
          <v-btn color="primary" variant="tonal" :loading="busy === 'model'" @click="saveModel">
            保存并加入模型池
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
              保存后会同时开启「群聊回复」，之后可在 Agent 面板继续细调。
            </div>
          </template>
          <template v-else>
            <div class="text-caption mb-3" style="opacity: 0.7">
              用别人分享的预设：导入 .zip 后一键应用人格 / 模型池 / Agent 配置 / 随包模块
              （会以预设里的模型连接为准，覆盖第 2 步的设置）。
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
              已应用「{{ appliedPreset.name }}」v{{ appliedPreset.version }}：人格、模型、模块已就位。
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
        <v-card-title>完成前检查</v-card-title>
        <v-card-text>
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
            全部通过。去群里 @ 一次机器人试试。
          </v-alert>
          <v-alert v-else type="warning" variant="tonal" density="compact" class="mt-3">
            还有 {{ readiness.blockers.length }} 项没完成；可以回到对应步骤补，或稍后在总览页继续。
          </v-alert>
        </v-card-text>
        <v-card-actions>
          <v-btn variant="text" @click="step = 3">上一步</v-btn>
          <v-spacer />
          <v-btn variant="text" :loading="readiness.loading" @click="readiness.load()">重新检查</v-btn>
          <v-btn color="primary" :loading="busy === 'finish'" @click="finish">完成</v-btn>
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

.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  word-break: break-all;
}
</style>
