<script setup lang="ts">
/**
 * 首启向导（4 步）：连接 → 模型 → 人格 → 完成。
 *
 * 设计约束（docs/oneclick-bundle-and-onboarding-design.md §7.2）：
 * - 每一步都只有一个问题，且**同屏完成**（例如"拉模型"和"入池"不再分两页）；
 * - 「下一步」只在当前步真的完成时可用；可「稍后再配」但总览会给出 blocker；
 * - 第 4 步的清单与总览就绪卡、启动横幅同源（`/api/readiness`）。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import http, { errorMessage } from '@/api/http'
import { useBotsStore } from '@/stores/bots'
import { useReadinessStore } from '@/stores/readiness'
import { useNotifyStore } from '@/stores/notify'

const router = useRouter()
const bots = useBotsStore()
const readiness = useReadinessStore()
const notify = useNotifyStore()

const step = ref(1)
const STEPS = ['连接账号', '配置模型', '人格设定', '完成']
const busy = ref('')

// ---------- 第 1 步：连接 ----------
const account = reactive({
  id: '',
  name: '',
  conn_mode: 'reverse' as 'forward' | 'reverse',
  ws_url: '',
  access_token: '',
})
const createdAccount = ref<{ id: string; ws_url: string; conn_mode: string } | null>(null)
const accountConnected = ref(false)
const connectTries = ref(0)

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

const currentAccountId = computed(() => createdAccount.value?.id || bots.currentAccountId)

function copy(text: string) {
  if (!text) return
  void navigator.clipboard?.writeText(text)
  notify.push('已复制', 'success')
}

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
async function createAndConnect() {
  await run('account', async () => {
    const result = await bots.addBotConfig({
      id: account.id.trim() || undefined,
      name: account.name.trim() || undefined,
      conn_mode: account.conn_mode,
      ws_url: account.ws_url.trim() || undefined,
    })
    createdAccount.value = {
      id: result.id,
      ws_url: result.ws_url,
      conn_mode: result.conn_mode,
    }
    bots.selectBot(result.id)
    await bots.connect(result.id)
    await pollConnected()
  })
}

async function pollConnected() {
  accountConnected.value = false
  for (let i = 0; i < 20; i += 1) {
    connectTries.value = i + 1
    await new Promise((resolve) => setTimeout(resolve, 1000))
    const list = await bots.fetchBots()
    const bot = list.find((item) => item.id === createdAccount.value?.id)
    if (bot?.status === 'connected') {
      accountConnected.value = true
      notify.push('账号已连接', 'success')
      return
    }
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
  if (bots.currentAccountId) {
    createdAccount.value = { id: bots.currentAccountId, ws_url: '', conn_mode: 'forward' }
    const current = bots.bots.find((item) => item.id === bots.currentAccountId)
    accountConnected.value = current?.status === 'connected'
  }
  await readiness.load()
})
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
          <v-alert type="info" variant="tonal" density="compact" class="mb-3">
            <div class="text-caption">
              需要先装好协议端（NapCat 等）。本程序只负责连接：填下面地址与 Token，
              或在协议端里配置反向 WebSocket 连到本程序。
            </div>
          </v-alert>

          <v-row dense>
            <v-col cols="12" md="6">
              <v-select
                v-model="account.conn_mode"
                :items="[
                  { title: '反向 WebSocket（本程序监听，推荐）', value: 'reverse' },
                  { title: '正向 WebSocket（本程序连出）', value: 'forward' },
                ]"
                label="连接方式"
                density="comfortable"
                variant="outlined"
              />
            </v-col>
            <v-col cols="12" md="6">
              <v-text-field
                v-model="account.name"
                label="账号名称（可选）"
                placeholder="例如：主号"
                density="comfortable"
                variant="outlined"
              />
            </v-col>
          </v-row>

          <v-text-field
            v-model="account.ws_url"
            :label="account.conn_mode === 'reverse' ? '监听地址（留空自动生成）' : 'WebSocket 地址'"
            :placeholder="
              account.conn_mode === 'reverse'
                ? 'ws://0.0.0.0:3001/onebot/v11/ws'
                : 'ws://127.0.0.1:3001/'
            "
            density="comfortable"
            variant="outlined"
          />
          <v-text-field
            v-model="account.access_token"
            label="Access Token（可选）"
            density="comfortable"
            variant="outlined"
            autocomplete="off"
          />

          <div v-if="createdAccount" class="created-box">
            <div class="text-caption mb-1" style="opacity: 0.7">把这个地址填到协议端（反向 WebSocket）：</div>
            <div class="d-flex align-center flex-wrap gap-2">
              <code class="mono">{{ createdAccount.ws_url || '（未返回地址）' }}</code>
              <v-btn size="x-small" variant="text" prepend-icon="mdi-content-copy" @click="copy(createdAccount.ws_url)">
                复制
              </v-btn>
            </div>
            <div class="text-caption mt-2" style="opacity: 0.7">
              状态：{{ accountConnected ? '✓ 已连接' : `等待对端接入…（第 ${connectTries} 次检查）` }}
            </div>
          </div>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn
            color="primary"
            variant="tonal"
            :loading="busy === 'account'"
            @click="createAndConnect"
          >
            {{ createdAccount ? '重新检查连接' : '创建账号并连接' }}
          </v-btn>
          <v-btn color="primary" :disabled="!accountConnected" @click="step = 2">下一步</v-btn>
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
          <v-btn color="primary" :disabled="!modelResult" @click="step = 3">下一步</v-btn>
        </v-card-actions>
      </v-card>

      <!-- 第 3 步 -->
      <v-card v-else-if="step === 3" variant="outlined">
        <v-card-title>给它一个人设</v-card-title>
        <v-card-text>
          <v-tabs v-model="personaMode" density="compact" class="mb-3">
            <v-tab value="scratch">从零写</v-tab>
            <v-tab value="bundle">选套装（即将支持）</v-tab>
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
            <v-alert type="info" variant="tonal" density="compact">
              套装（整合包）功能在开发中；现在可以先用「从零写」或稍后在 Agent 面板配置。
            </v-alert>
          </template>
        </v-card-text>
        <v-card-actions>
          <v-btn variant="text" @click="step = 2">上一步</v-btn>
          <v-spacer />
          <v-btn color="primary" variant="tonal" :loading="busy === 'persona'" @click="savePersona">
            保存并应用
          </v-btn>
          <v-btn color="primary" :disabled="!personaDone" @click="step = 4">下一步</v-btn>
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
