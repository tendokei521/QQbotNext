<script setup lang="ts">
import { onMounted, onUnmounted, reactive, ref } from 'vue'
import { useBotsStore, displayName, resolveAccount, resolvedBotId, type BotConfig, type BotConnMode, type BotStatus } from '@/stores/bots'
import { useNotifyStore } from '@/stores/notify'
import { errorMessage } from '@/api/http'
import EmptyState from '@/components/EmptyState.vue'
import AddBotDialog from '@/components/bots/AddBotDialog.vue'

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

interface Draft {
  /** 展示名：可随时改；id 不可改，二者不是一个东西 */
  name: string
  ws_url: string
  access_token: string
  auto_connect: boolean
  /** 连接方向在「添加账号」时选定，卡片上只读展示、不提供切换 */
  conn_mode: BotConnMode
}

/** 保存请求体：后端已不再使用 owner_id；缺这个字段时沿用旧值，所以这里不发送它 */
type BotConfigPayload = Omit<BotConfig, 'owner_id'>

// 编辑草稿：account_id -> 表单字段（id 不可变，所以用它当 key 而不是数组下标）
const drafts = reactive<Record<string, Draft>>({})
const saving = ref(false)
const loadingCards = ref(false)
const deleteTarget = ref<string | null>(null)
const addDialog = ref(false)
const acting = reactive<Record<string, boolean>>({})
let saveTimer: ReturnType<typeof setTimeout> | null = null

function draftOf(id: string) {
  if (!drafts[id]) {
    drafts[id] = { name: '', ws_url: '', access_token: '', auto_connect: false, conn_mode: 'forward' }
  }
  return drafts[id]
}

/** 反向账号：监听已就绪但协议端还没连进来时，别让人以为连错了 */
function statusText(b: { status: BotStatus; conn_mode?: BotConnMode; listening?: boolean }) {
  if (b.conn_mode === 'reverse' && b.status !== 'connected' && b.listening) return '等待协议端接入'
  return STATUS_TEXT[b.status] ?? b.status
}

/** 连接方向自创建时固定，卡片据此调整按钮禁用条件（反向监听已就绪时不必再点「连接」） */
function isReverseDraft(id: string): boolean {
  return draftOf(id).conn_mode === 'reverse'
}

async function loadCards() {
  loadingCards.value = true
  try {
    const [configs, statuses] = await Promise.all([bots.fetchBotConfig(), bots.fetchBots()])
    // 以配置列表为主填充草稿：两者都按 account id 对齐（id 就是身份，没有下标兜底）
    const seen = new Set<string>()
    configs.forEach((cfg) => {
      const id = cfg.id
      if (!id) return
      seen.add(id)
      const b = statuses.find((s) => s.id === id)
      drafts[id] = {
        name: cfg.name ?? '',
        ws_url: cfg.ws_url ?? b?.ws_url ?? '',
        access_token: cfg.access_token ?? '',
        auto_connect: cfg.auto_connect ?? b?.auto_connect ?? false,
        conn_mode: (cfg.conn_mode ?? b?.conn_mode ?? 'forward') === 'reverse' ? 'reverse' : 'forward',
      }
    })
    // 清理已删除/失效的草稿，避免残留旧条目被再次全量保存而“复活”账号
    for (const key of Object.keys(drafts)) {
      if (!seen.has(key)) delete drafts[key]
    }
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    loadingCards.value = false
  }
}

function buildPayload(): BotConfigPayload[] {
  return Object.entries(drafts).map(([id, d]) => ({
    id,
    name: d.name.trim() || id,
    ws_url: d.ws_url.trim(),
    access_token: d.access_token,
    auto_connect: d.auto_connect,
    conn_mode: d.conn_mode,
  }))
}

async function persistDrafts(showToast = true): Promise<boolean> {
  if (saving.value) return false
  saving.value = true
  try {
    // 类型上 BotConfig 仍把 owner_id 标为必填，但后端允许缺省（沿用旧值），这里显式转换
    await bots.saveBotConfig(buildPayload() as unknown as BotConfig[])
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
  // 取消挂起的自动保存，避免手动保存后 timer 再重复全量写一次
  if (saveTimer) {
    clearTimeout(saveTimer)
    saveTimer = null
  }
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

/** 「添加账号」先弹选类型（正向 / 反向），选定后才创建（见 AddBotDialog） */
function addBot() {
  addDialog.value = true
}

/** 新账号已创建：刷新卡片并选中它，方便直接填 Token / 地址 */
async function onBotCreated(accountId: string) {
  await loadCards()
  bots.selectBot(accountId)
}

async function doDelete() {
  if (deleteTarget.value === null) return
  // 立即取消挂起的自动保存，防止删除竞态把被删账号重新写回
  if (saveTimer) {
    clearTimeout(saveTimer)
    saveTimer = null
  }
  try {
    await bots.deleteBotConfig(deleteTarget.value)
    notify.push('账号已删除', 'success')
    deleteTarget.value = null
    // loadCards 已同时拉取配置与状态，无需再单独 fetchBots
    await loadCards()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

async function act(accountId: string, kind: 'connect' | 'disconnect' | 'reconnect') {
  if (acting[accountId]) return
  acting[accountId] = true
  try {
    if (kind === 'connect') await bots.connect(accountId)
    else if (kind === 'disconnect') await bots.disconnect(accountId)
    else await bots.reconnect(accountId)
    notify.push(kind === 'connect' ? '已连接' : kind === 'disconnect' ? '已断开' : '已重连', 'success')
    await bots.fetchBots()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    acting[accountId] = false
  }
}

onMounted(loadCards)
onUnmounted(() => {
  if (saveTimer) {
    clearTimeout(saveTimer)
    saveTimer = null
  }
})
</script>

<template>
  <div>
    <div class="app-page-header">
      <div>
        <h1 class="app-page-title">账号管理</h1>
        <div class="app-page-subtitle">添加并管理要接入的 QQ 账号</div>
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
      <EmptyState icon="mdi-robot-off-outline" title="还没有账号" description="先添加一个 QQ 账号，再把它连起来">
        <v-btn color="primary" variant="tonal" prepend-icon="mdi-plus" @click="addBot">新增账号</v-btn>
      </EmptyState>
    </v-card>

    <v-row v-else>
      <v-col v-for="b in bots.bots" :key="b.id" cols="12" md="6" xl="4">
        <v-card variant="outlined" class="bot-card app-card-fill">
          <v-card-item>
            <template #prepend>
              <v-avatar color="lightprimary" size="40" rounded="lg">
                <v-icon icon="mdi-robot" color="primary" />
              </v-avatar>
            </template>
            <v-card-title class="text-body-1 font-weight-bold app-line-clamp-1">
              {{ displayName(b) }}<template v-if="resolvedBotId(b)"> · QQ {{ resolvedBotId(b) }}</template>
            </v-card-title>
            <v-card-subtitle class="d-flex align-center gap-2">
              <v-chip size="x-small" :color="statusColor(b.status)" variant="tonal">
                <v-icon start size="x-small" icon="mdi-circle" /> {{ statusText(b) }}
              </v-chip>
              <span v-if="resolveAccount(b)?.nickname" class="text-caption app-line-clamp-1">
                {{ resolveAccount(b)!.nickname }}<template v-if="Number(resolveAccount(b)!.user_id) > 0"> ({{ resolveAccount(b)!.user_id }})</template>
              </span>
            </v-card-subtitle>
            <template #append>
              <v-btn variant="text" icon="mdi-delete-outline" color="error" size="small" title="删除此账号配置" @click="deleteTarget = b.id" />
            </template>
          </v-card-item>

          <v-card-text>
            <v-text-field
              :model-value="draftOf(b.id).name"
              label="名称"
              placeholder="给这个账号起个名字"
              density="comfortable"
              hide-details
              class="mb-3"
              @update:model-value="(v: string) => { draftOf(b.id).name = v }"
              @blur="scheduleSave()"
            />
            <v-text-field
              :model-value="draftOf(b.id).ws_url"
              :label="draftOf(b.id).conn_mode === 'reverse' ? '本机监听地址' : '协议端地址'"
              :placeholder="draftOf(b.id).conn_mode === 'reverse' ? 'ws://0.0.0.0:3001/onebot/v11/ws' : 'ws://127.0.0.1:3001/'"
              density="comfortable"
              hide-details="auto"
              class="mb-3"
              @update:model-value="(v: string) => { draftOf(b.id).ws_url = v }"
              @blur="scheduleSave()"
            />
            <v-text-field
              :model-value="draftOf(b.id).access_token"
              label="连接令牌（可选）"
              placeholder="没有就留空"
              hint="与协议端里填的一致，没有就留空"
              persistent-hint
              density="comfortable"
              hide-details="auto"
              autocomplete="off"
              class="mb-3"
              @update:model-value="(v: string) => { draftOf(b.id).access_token = v }"
              @blur="scheduleSave()"
            />
            <v-switch
              :model-value="draftOf(b.id).auto_connect"
              label="启动时自动连接"
              color="primary"
              density="compact"
              hide-details
              @update:model-value="(v: boolean | null) => { draftOf(b.id).auto_connect = !!v }"
              @change="scheduleSave()"
            />
            <v-divider class="my-3" />
            <div class="info-grid">
              <div>
                <div class="info-label">当前 QQ</div>
                <div class="info-value">
                  <template v-if="resolvedBotId(b)">{{ resolvedBotId(b) }}</template>
                  <template v-else>未连接</template>
                  <span v-if="!b.bot_id && resolvedBotId(b)" class="info-hint">上次连接</span>
                </div>
              </div>
              <div>
                <div class="info-label">账号标识</div>
                <div class="info-value">{{ b.id }}</div>
              </div>
              <div v-if="draftOf(b.id).conn_mode === 'reverse'">
                <div class="info-label">监听</div>
                <div class="info-value">{{ b.listening ? '监听中' : '未监听' }}</div>
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
            <v-btn color="success" variant="tonal" size="small" prepend-icon="mdi-plug" :loading="acting[b.id]" :disabled="acting[b.id] || b.status === 'connected' || (isReverseDraft(b.id) && !!b.listening)" @click="act(b.id, 'connect')">连接</v-btn>
            <v-btn color="error" variant="tonal" size="small" prepend-icon="mdi-unlink" :disabled="acting[b.id] || (b.status !== 'connected' && !(isReverseDraft(b.id) && b.listening))" @click="act(b.id, 'disconnect')">断开</v-btn>
            <v-btn color="warning" variant="tonal" size="small" prepend-icon="mdi-restart" :disabled="acting[b.id]" @click="act(b.id, 'reconnect')">重连</v-btn>
          </v-card-actions>
        </v-card>
      </v-col>
    </v-row>

    <v-dialog :model-value="deleteTarget !== null" max-width="400" @update:model-value="(v: boolean) => { if (!v) deleteTarget = null }">
      <v-card>
        <v-card-title>删除账号配置</v-card-title>
        <v-card-text>确定删除该账号的配置吗？此操作不可撤销。</v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="deleteTarget = null">取消</v-btn>
          <v-btn color="error" variant="tonal" @click="doDelete">删除</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 新增账号：先选连接方式（正向 / 反向），选定后才创建 -->
    <AddBotDialog v-model="addDialog" @created="onBotCreated" />
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

/* 「上次连接」标注：明确该账号来自快照而非当前实时连接 */
.info-hint {
  margin-left: 6px;
  font-size: 11px;
  font-weight: 400;
  color: rgba(var(--v-theme-on-surface), 0.45);
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
