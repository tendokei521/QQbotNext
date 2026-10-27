<script setup lang="ts">
/**
 * 协议端面板：装版本 → 启动 → 扫码登录 → 自动配置账号。
 *
 * 这里不重复协议端自己的面板；它只回答用户真正会问的四件事：
 * 装没装、起没起、为什么退、怎么登录。
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useBotsStore } from '@/stores/bots'
import { useNotifyStore } from '@/stores/notify'
import { useProtocolStore, type ProtocolItem } from '@/stores/protocol'
import { errorMessage } from '@/api/http'

const protocol = useProtocolStore()
const bots = useBotsStore()
const notify = useNotifyStore()

const qrDialog = ref(false)
const qrTarget = ref<ProtocolItem | null>(null)
const qrImage = ref('')
const qrContent = ref('')
const loginState = ref<{ online: boolean; nickname: string; user_id: number | string } | null>(null)
const polling = ref<number | null>(null)

const logDialog = ref(false)
const logText = ref('')
const logTarget = ref<ProtocolItem | null>(null)

const currentAccount = computed(() => bots.currentAccountId)

function statusText(item: ProtocolItem): string {
  if (item.running) return '运行中'
  if (item.installed) return '已安装 · 未运行'
  return '未安装'
}

function statusColor(item: ProtocolItem): string {
  return item.running ? 'success' : item.installed ? 'warning' : 'grey'
}

async function run(task: () => Promise<void>) {
  try {
    await task()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

async function install(item: ProtocolItem) {
  await run(async () => {
    await protocol.install(item.protocol, item.versions[0])
    notify.push(`${item.display_name} 已安装`, 'success')
  })
}

async function toggle(item: ProtocolItem) {
  await run(async () => {
    if (item.running) await protocol.stop(item.protocol)
    else await protocol.start(item.protocol)
    notify.push(item.running ? '已停止' : '已启动', 'success')
  })
}

async function restart(item: ProtocolItem) {
  await run(async () => {
    await protocol.restart(item.protocol)
    notify.push('已重启', 'success')
  })
}

async function openLog(item: ProtocolItem) {
  logTarget.value = item
  logText.value = await protocol.log(item.protocol, 200)
  logDialog.value = true
}

async function openQr(item: ProtocolItem) {
  qrTarget.value = item
  qrDialog.value = true
  qrImage.value = ''
  qrContent.value = ''
  loginState.value = null
  await run(async () => {
    const result = await protocol.qrcode(item.protocol)
    qrImage.value = result.image
    qrContent.value = result.content
    startPolling()
  })
}

function startPolling() {
  stopPolling()
  polling.value = window.setInterval(async () => {
    if (!qrTarget.value) return
    try {
      loginState.value = await protocol.loginStatus(qrTarget.value.protocol)
      if (loginState.value.online) {
        stopPolling()
        notify.push(`已登录：${loginState.value.nickname || loginState.value.user_id}`, 'success')
      }
    } catch {
      /* 轮询失败不打扰用户，下一轮再试 */
    }
  }, 2000)
}

function stopPolling() {
  if (polling.value !== null) {
    window.clearInterval(polling.value)
    polling.value = null
  }
}

function closeQr() {
  qrDialog.value = false
  stopPolling()
}

async function finishLogin() {
  if (!qrTarget.value || !currentAccount.value) {
    notify.push('先在顶部选择一个账号', 'warning')
    return
  }
  await run(async () => {
    await protocol.completeLogin(qrTarget.value!.protocol, currentAccount.value!)
    await bots.fetchBots()
    notify.push('账号已自动配置为反向 WebSocket', 'success')
    closeQr()
  })
}

onMounted(async () => {
  if (!bots.bots.length) await bots.fetchBots()
  await protocol.load()
})

onUnmounted(stopPolling)
</script>

<template>
  <div>
    <div class="app-page-header" style="align-items: center">
      <div>
        <h1 class="app-page-title">
          <v-icon icon="mdi-console-network-outline" size="28" color="primary" class="mr-1" />
          协议端
        </h1>
        <div class="app-page-subtitle">NapCat 等协议端的安装、启停与扫码登录</div>
      </div>
      <v-btn variant="tonal" prepend-icon="mdi-refresh" :loading="protocol.loading" @click="protocol.load()">
        刷新
      </v-btn>
    </div>

    <v-alert type="info" variant="tonal" density="compact" class="mb-4">
      <div class="text-caption">
        协议端负责登录 QQ，本程序只通过 WebSocket 连它。这里能直接装清单里的固定版本、
        启动并扫码，登录成功后会自动把账号配成反向 WebSocket。
      </div>
    </v-alert>

    <v-progress-linear v-if="protocol.loading" indeterminate color="primary" class="mb-3" />

    <v-row>
      <v-col v-for="item in protocol.items" :key="item.protocol" cols="12" md="6">
        <v-card variant="outlined" class="protocol-card">
          <v-card-item>
            <template #prepend>
              <v-avatar color="lightprimary" size="40" rounded="lg">
                <v-icon icon="mdi-robot-outline" color="primary" />
              </v-avatar>
            </template>
            <v-card-title class="text-body-1 font-weight-bold">{{ item.display_name }}</v-card-title>
            <v-card-subtitle>
              <v-chip size="x-small" :color="statusColor(item)" variant="tonal">{{ statusText(item) }}</v-chip>
              <span v-if="item.version" class="ml-2">v{{ item.version }}</span>
            </v-card-subtitle>
          </v-card-item>

          <v-card-text class="text-caption" style="opacity: 0.75">
            <div v-if="item.versions.length">可装版本：{{ item.versions.join('、') }}</div>
            <div v-else>还没有配置可装版本（在 data/protocol/manifest.json 里加固定版本）</div>
            <div v-if="item.running">PID {{ item.pid }} · 已运行 {{ item.uptime_s }}s</div>
            <div v-if="item.restarts">自动重启 {{ item.restarts }} 次</div>
            <div v-if="item.last_exit?.reason === 'crashed'" class="text-error">
              上次异常退出（exit {{ item.last_exit.code }}），请看日志
            </div>
          </v-card-text>

          <v-card-actions class="flex-wrap">
            <v-btn
              v-if="!item.installed && item.versions.length"
              size="small"
              color="primary"
              variant="tonal"
              :loading="protocol.busy === `${item.protocol}:install`"
              @click="install(item)"
            >
              安装 v{{ item.versions[0] }}
            </v-btn>
            <v-btn
              v-if="item.installed"
              size="small"
              :color="item.running ? 'warning' : 'primary'"
              variant="tonal"
              :loading="protocol.busy === `${item.protocol}:${item.running ? 'stop' : 'start'}`"
              @click="toggle(item)"
            >
              {{ item.running ? '停止' : '启动' }}
            </v-btn>
            <v-btn v-if="item.running" size="small" variant="text" prepend-icon="mdi-restart" @click="restart(item)">
              重启
            </v-btn>
            <v-btn size="small" variant="text" prepend-icon="mdi-qrcode" @click="openQr(item)">扫码登录</v-btn>
            <v-btn size="small" variant="text" prepend-icon="mdi-text-box-outline" @click="openLog(item)">日志</v-btn>
          </v-card-actions>
        </v-card>
      </v-col>
    </v-row>

    <v-dialog v-model="qrDialog" max-width="460" @update:model-value="(v: boolean) => !v && closeQr()">
      <v-card>
        <v-card-title class="d-flex align-center">
          <v-icon icon="mdi-qrcode-scan" class="mr-2" color="primary" /> 扫码登录
        </v-card-title>
        <v-card-text class="text-center">
          <div v-if="loginState?.online" class="py-4">
            <v-icon icon="mdi-check-circle" size="48" color="success" />
            <div class="mt-2">已登录：{{ loginState.nickname || loginState.user_id }}</div>
          </div>
          <template v-else>
            <v-img v-if="qrImage" :src="qrImage" max-width="240" class="mx-auto qr-image" />
            <div v-else-if="qrContent" class="text-caption" style="word-break: break-all">{{ qrContent }}</div>
            <div v-else class="text-caption py-6">正在获取二维码…</div>
            <div class="text-caption mt-3" style="opacity: 0.7">
              用手机 QQ 扫码；登录成功后这里会自动变成「已登录」。
            </div>
          </template>
        </v-card-text>
        <v-card-actions>
          <v-btn variant="text" @click="closeQr">关闭</v-btn>
          <v-spacer />
          <v-btn color="primary" variant="tonal" :disabled="!loginState?.online" @click="finishLogin">
            完成并配置账号
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog v-model="logDialog" max-width="760">
      <v-card>
        <v-card-title class="d-flex align-center">
          <v-icon icon="mdi-text-box-outline" class="mr-2" color="primary" />
          {{ logTarget?.display_name }} 日志
          <v-spacer />
          <v-btn size="small" variant="text" prepend-icon="mdi-refresh" @click="logTarget && openLog(logTarget)">
            刷新
          </v-btn>
        </v-card-title>
        <v-card-text>
          <pre class="log-box">{{ logText || '（暂无日志）' }}</pre>
          <div class="text-caption mt-2" style="opacity: 0.6">{{ logTarget?.log_path }}</div>
        </v-card-text>
      </v-card>
    </v-dialog>
  </div>
</template>

<style scoped>
.protocol-card {
  height: 100%;
  border-radius: 14px;
}

.qr-image {
  border-radius: 12px;
}

.log-box {
  max-height: 420px;
  overflow: auto;
  font-size: 12px;
  line-height: 1.5;
  background: rgba(var(--v-theme-on-surface), 0.04);
  border-radius: 10px;
  padding: 12px;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
