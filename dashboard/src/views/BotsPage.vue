<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useBotsStore, type BotConfig, type BotStatus } from '@/stores/bots'
import { useNotifyStore } from '@/stores/notify'
import { errorMessage } from '@/api/http'
import EmptyState from '@/components/EmptyState.vue'

const bots = useBotsStore()
const notify = useNotifyStore()

const STATUS_TEXT: Record<BotStatus, string> = {
  connected: '已连接',
  disconnected: '离线',
  connecting: '连接中…',
  reconnecting: '重连中…',
  error: '错误',
}

const statusColor = (s: BotStatus) =>
  s === 'connected' ? 'success' : s === 'connecting' || s === 'reconnecting' ? 'warning' : 'error'

// 编辑草稿：index -> 表单字段
const drafts = reactive<Record<number, { ws_url: string; access_token: string; owner_id: string; auto_connect: boolean }>>({})
const saving = ref(false)
const loadingCards = ref(false)
const deleteTarget = ref<number | null>(null)
let saveTimer: ReturnType<typeof setTimeout> | null = null

function draftOf(index: number) {
  if (!drafts[index]) {
    drafts[index] = { ws_url: '', access_token: '', owner_id: '', auto_connect: false }
  }
  return drafts[index]
}

async function loadCards() {
  loadingCards.value = true
  try {
    const [configs, statuses] = await Promise.all([bots.fetchBotConfig(), bots.fetchBots()])
    // 以配置列表为主填充草稿：/api/bots/config 返回的 index 缺失时按列表位置兜底
    configs.forEach((cfg, pos) => {
      const index = Number(cfg.index ?? pos)
      const b = statuses.find((s) => s.index === index)
      drafts[index] = {
        ws_url: cfg.ws_url ?? b?.ws_url ?? '',
        access_token: cfg.access_token ?? '',
        owner_id: cfg.owner_id !== undefined && cfg.owner_id !== null ? String(cfg.owner_id) : (b?.owner_id !== undefined && b.owner_id !== null ? String(b.owner_id) : ''),
        auto_connect: cfg.auto_connect ?? b?.auto_connect ?? false,
      }
    })
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    loadingCards.value = false
  }
}

function buildPayload(): BotConfig[] {
  return Object.entries(drafts).map(([index, d]) => ({
    index: parseInt(index, 10),
    ws_url: d.ws_url.trim(),
    access_token: d.access_token,
    owner_id: d.owner_id.trim() ? parseInt(d.owner_id.trim(), 10) : null,
    auto_connect: d.auto_connect,
  }))
}

async function persistDrafts(showToast = true): Promise<boolean> {
  if (saving.value) return false
  saving.value = true
  try {
    await bots.saveBotConfig(buildPayload())
    if (showToast) notify.push('配置已保存', 'success')
    return true
  } catch (err) {
    notify.push(errorMessage(err), 'error')
    return false
  } finally {
    saving.value = false
  }
}

async function saveAll() {
  if (await persistDrafts(true)) {
    await loadCards()
  }
}

/** 输入框失焦后自动保存一次（防抖，避免连续切换输入框时重复提交） */
function scheduleSave() {
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = setTimeout(() => {
    saveTimer = null
    void persistDrafts(false)
  }, 400)
}

async function addBot() {
  try {
    const result: any = await bots.addBotConfig({})
    notify.push('已添加新账号配置', 'success')
    await loadCards()
    // 选中新账号
    if (result && result.index !== undefined) bots.selectBot(result.index)
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

async function doDelete() {
  if (deleteTarget.value === null) return
  try {
    await bots.deleteBotConfig(deleteTarget.value)
    notify.push('已删除账号配置', 'success')
    deleteTarget.value = null
    await Promise.all([loadCards(), bots.fetchBots()])
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

async function act(index: number, kind: 'connect' | 'disconnect' | 'reconnect') {
  try {
    if (kind === 'connect') await bots.connect(index)
    else if (kind === 'disconnect') await bots.disconnect(index)
    else await bots.reconnect(index)
    notify.push(kind === 'connect' ? '已连接' : kind === 'disconnect' ? '已断开' : '已重连', 'success')
    await bots.fetchBots()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

onMounted(loadCards)
</script>

<template>
  <div>
    <div class="app-page-header">
      <div>
        <h1 class="app-page-title">账号管理</h1>
        <div class="app-page-subtitle">WebSocket 连接配置与账号状态</div>
      </div>
      <div class="d-flex gap-2">
        <v-btn variant="tonal" prepend-icon="mdi-refresh" :loading="loadingCards" @click="loadCards">刷新</v-btn>
        <v-btn color="primary" prepend-icon="mdi-plus" @click="addBot">新增账号</v-btn>
        <v-btn color="primary" variant="tonal" prepend-icon="mdi-content-save" :loading="saving" @click="saveAll">
          保存所有配置
        </v-btn>
      </div>
    </div>

    <v-row v-if="loadingCards && bots.bots.length === 0">
      <v-col v-for="i in 3" :key="i" cols="12" md="6" xl="4">
        <v-skeleton-loader type="card-avatar, article, actions" class="rounded-xl" />
      </v-col>
    </v-row>

    <v-card v-else-if="bots.bots.length === 0" variant="outlined">
      <EmptyState icon="mdi-robot-off-outline" title="暂无账号配置" description="点击「新增账号」添加第一个 WebSocket 连接">
        <v-btn color="primary" variant="tonal" prepend-icon="mdi-plus" @click="addBot">新增账号</v-btn>
      </EmptyState>
    </v-card>

    <v-row v-else>
      <v-col v-for="b in bots.bots" :key="b.index" cols="12" md="6" xl="4">
        <v-card variant="outlined" class="bot-card app-card-fill">
          <v-card-item>
            <template #prepend>
              <v-avatar color="lightprimary" size="40" rounded="lg">
                <v-icon icon="mdi-robot" color="primary" />
              </v-avatar>
            </template>
            <v-card-title class="text-body-1 font-weight-bold">
              账号 #{{ b.index }}{{ b.bot_id ? ` · Bot ${b.bot_id}` : '' }}
            </v-card-title>
            <v-card-subtitle class="d-flex align-center gap-2">
              <v-chip size="x-small" :color="statusColor(b.status)" variant="tonal">
                <v-icon start size="x-small" icon="mdi-circle" /> {{ STATUS_TEXT[b.status] }}
              </v-chip>
              <span v-if="b.login_info?.nickname" class="text-caption app-line-clamp-1">{{ b.login_info.nickname }} ({{ b.login_info.user_id }})</span>
            </v-card-subtitle>
            <template #append>
              <v-btn variant="text" icon="mdi-delete-outline" color="error" size="small" title="删除此账号配置" @click="deleteTarget = b.index" />
            </template>
          </v-card-item>

          <v-card-text>
            <v-text-field
              :model-value="draftOf(b.index).ws_url"
              label="WebSocket 地址"
              placeholder="ws://…"
              density="comfortable"
              hide-details
              class="mb-3"
              @update:model-value="(v: string) => { draftOf(b.index).ws_url = v }"
              @blur="scheduleSave()"
            />
            <v-text-field
              :model-value="draftOf(b.index).access_token"
              label="Access Token"
              placeholder="无 Token"
              density="comfortable"
              hide-details
              autocomplete="off"
              class="mb-3"
              @update:model-value="(v: string) => { draftOf(b.index).access_token = v }"
              @blur="scheduleSave()"
            />
            <v-text-field
              :model-value="draftOf(b.index).owner_id"
              label="Owner ID"
              placeholder="管理员QQ号"
              density="comfortable"
              hide-details
              class="mb-3"
              @update:model-value="(v: string) => { draftOf(b.index).owner_id = v }"
              @blur="scheduleSave()"
            />
            <v-switch
              :model-value="draftOf(b.index).auto_connect"
              label="启动时自动连接"
              color="primary"
              density="compact"
              hide-details
              @update:model-value="(v: boolean | null) => { draftOf(b.index).auto_connect = !!v }"
            />
            <v-divider class="my-3" />
            <div class="info-grid">
              <div>
                <div class="info-label">Bot ID</div>
                <div class="info-value">{{ b.bot_id || '未连接' }}</div>
              </div>
              <div>
                <div class="info-label">索引</div>
                <div class="info-value">#{{ b.index }}</div>
              </div>
              <div v-if="b.reconnect_attempts !== undefined">
                <div class="info-label">重连次数</div>
                <div class="info-value">{{ b.reconnect_attempts }}</div>
              </div>
            </div>
            <div v-if="b.last_error" class="error-tip">
              <v-icon icon="mdi-alert-circle-outline" size="small" /> {{ b.last_error }}
            </div>
          </v-card-text>

          <v-card-actions>
            <v-btn color="success" variant="tonal" size="small" prepend-icon="mdi-plug" :disabled="b.status === 'connected'" @click="act(b.index, 'connect')">连接</v-btn>
            <v-btn color="error" variant="tonal" size="small" prepend-icon="mdi-unlink" :disabled="b.status !== 'connected'" @click="act(b.index, 'disconnect')">断开</v-btn>
            <v-btn color="warning" variant="tonal" size="small" prepend-icon="mdi-restart" @click="act(b.index, 'reconnect')">重连</v-btn>
          </v-card-actions>
        </v-card>
      </v-col>
    </v-row>

    <v-dialog :model-value="deleteTarget !== null" max-width="400" @update:model-value="(v: boolean) => { if (!v) deleteTarget = null }">
      <v-card>
        <v-card-title>删除账号配置</v-card-title>
        <v-card-text>确定删除账号 #{{ deleteTarget }} 的配置吗？此操作不可撤销。</v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="deleteTarget = null">取消</v-btn>
          <v-btn color="error" variant="tonal" @click="doDelete">删除</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<style scoped>
.bot-card {
  border-radius: 16px;
}

.info-grid {
  display: flex;
  gap: 24px;
}

.info-label {
  font-size: 11px;
  color: rgba(var(--v-theme-on-surface), 0.45);
  text-transform: uppercase;
  letter-spacing: 0.3px;
}

.info-value {
  font-size: 13.5px;
  font-weight: 500;
}

.error-tip {
  margin-top: 8px;
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12.5px;
  color: rgb(var(--v-theme-error));
  background: rgba(var(--v-theme-error), 0.08);
  border-radius: 8px;
  padding: 8px 10px;
  word-break: break-all;
}
</style>
