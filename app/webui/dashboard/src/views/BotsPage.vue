<script setup lang="ts">
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useBotsStore, displayName, resolveAccount, resolvedBotId, type BotConfig, type BotConnMode, type BotData, type BotStatus } from '@/stores/bots'
import { useNotifyStore } from '@/stores/notify'
import { errorMessage } from '@/api/http'
import EmptyState from '@/components/EmptyState.vue'
import AddBotDialog from '@/components/bots/AddBotDialog.vue'
import EventMonitorDialog from '@/components/bots/EventMonitorDialog.vue'

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
/** 批量连接/断开：串行执行，按钮上显示进度，结束后汇总失败项 */
const batchBusy = ref<'connect' | 'disconnect' | ''>('')
const batchProgress = reactive({ done: 0, total: 0 })
/** 有改动还没写回后端：页头提示，切页前自动补存一次 */
const dirty = ref(false)
const loadError = ref('')
/** 诊断台：看这个账号最近收到的协议端事件，定位「连上了但没反应」 */
const diagOpen = ref(false)
const diagAccountId = ref<string | null>(null)
const diagAccountName = ref('')
let saveTimer: ReturnType<typeof setTimeout> | null = null

function openDiagnostics(b: BotData) {
  diagAccountId.value = b.id
  diagAccountName.value = displayName(b)
  diagOpen.value = true
}

function markDirty() {
  dirty.value = true
}

function draftOf(id: string) {
  if (!drafts[id]) {
    drafts[id] = { name: '', ws_url: '', access_token: '', auto_connect: false, conn_mode: 'forward' }
  }
  return drafts[id]
}

/** 「协议端怎么填」展开状态：account_id -> 是否展开 */
const helpOpen = reactive<Record<string, boolean>>({})

function toggleHelp(id: string) {
  helpOpen[id] = !helpOpen[id]
}

/** 复制地址：非 https 的局域网地址下浏览器可能不给剪贴板权限，失败就让用户手动复制 */
async function copyAddress(text: string) {
  const value = text.trim()
  if (!value) {
    notify.push('地址还没填，先填好再复制', 'warning')
    return
  }
  try {
    await navigator.clipboard.writeText(value)
    notify.push('地址已复制', 'success')
  } catch {
    notify.push('复制失败，请手动选中地址复制', 'warning')
  }
}

/** 反向账号：监听已就绪但协议端还没连进来时，别让人以为连错了 */
function statusText(b: { status: BotStatus; conn_mode?: BotConnMode; listening?: boolean }) {
  if (b.conn_mode === 'reverse' && b.status !== 'connected' && b.listening) return '等待协议端接入'
  return STATUS_TEXT[b.status] ?? b.status
}

/** 等待协议端接入是正常过渡态，不能用「错误」红色 */
function statusChipColor(b: { status: BotStatus; conn_mode?: BotConnMode; listening?: boolean }) {
  if (b.conn_mode === 'reverse' && b.status !== 'connected' && b.listening) return 'warning'
  return statusColor(b.status)
}

/** 连接方向自创建时固定，卡片据此调整按钮禁用条件（反向监听已就绪时不必再点「连接」） */
function isReverseDraft(id: string): boolean {
  return draftOf(id).conn_mode === 'reverse'
}

async function loadCards() {
  loadingCards.value = true
  loadError.value = ''
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
    dirty.value = false
  } catch (err) {
    // 失败要让人看见并能重试，不要把「加载失败」伪装成「没有账号」
    loadError.value = errorMessage(err)
    notify.push(loadError.value, 'error')
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
    dirty.value = false
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
    notify.push(
      kind === 'connect' ? '已发送连接请求' : kind === 'disconnect' ? '已断开' : '已发送重连请求',
      'success',
    )
    await bots.fetchBots()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    acting[accountId] = false
  }
}

/** 账号是否已经「连着」：反向模式监听就绪也算，避免批量连接把已在等待的账号再连一遍 */
function isLive(b: BotData): boolean {
  return b.status === 'connected' || (b.conn_mode === 'reverse' && !!b.listening)
}

/** 批量连接/断开：逐个串行调用，失败不打断，最后汇总提示 */
async function batchAct(kind: 'connect' | 'disconnect') {
  if (batchBusy.value) return
  const targets = bots.bots.filter((b) => (kind === 'connect' ? !isLive(b) : isLive(b)))
  if (!targets.length) {
    notify.push(kind === 'connect' ? '所有账号都已经连接' : '所有账号都已经断开', 'info')
    return
  }
  const verb = kind === 'connect' ? '连接' : '断开'
  batchBusy.value = kind
  batchProgress.total = targets.length
  batchProgress.done = 0
  const failed: string[] = []
  for (const b of targets) {
    acting[b.id] = true
    try {
      if (kind === 'connect') await bots.connect(b.id)
      else await bots.disconnect(b.id)
    } catch (err) {
      failed.push(`${displayName(b)}：${errorMessage(err)}`)
    } finally {
      acting[b.id] = false
      batchProgress.done += 1
    }
  }
  batchBusy.value = ''
  try {
    await bots.fetchBots()
  } catch {
    // 状态刷新失败不影响本批结果，下次刷新会补齐
  }
  if (failed.length) {
    const shown = failed.slice(0, 2).join('；')
    notify.push(`${verb}完成，${failed.length} 个失败：${shown}${failed.length > 2 ? '…' : ''}`, 'warning')
  } else {
    notify.push(kind === 'connect' ? '全部连接请求已发送' : '全部已断开', 'success')
  }
}

onMounted(loadCards)
onBeforeUnmount(() => {
  // 切页前把挂起的自动保存补一次：否则最后 400ms 内的改动会丢
  if (saveTimer) {
    clearTimeout(saveTimer)
    saveTimer = null
  }
  if (dirty.value) void persistDrafts(false)
})
</script>

<template>
  <div>
    <div class="app-page-header">
      <div>
        <h1 class="app-page-title">账号管理</h1>
        <div class="app-page-subtitle">添加并管理要接入的 QQ 账号</div>
      </div>
      <div class="d-flex align-center gap-2">
        <v-chip v-if="dirty" size="small" color="warning" variant="tonal">
          <v-icon start size="x-small" icon="mdi-pencil-outline" /> 有未保存修改
        </v-chip>
        <v-btn variant="tonal" prepend-icon="mdi-refresh" :loading="loadingCards" @click="loadCards">刷新</v-btn>
        <v-btn color="primary" prepend-icon="mdi-plus" @click="addBot">新增账号</v-btn>
        <v-btn color="primary" variant="tonal" prepend-icon="mdi-content-save" :loading="saving" @click="saveAll">
          保存所有配置
        </v-btn>
      </div>
    </div>

    <div v-if="bots.bots.length" class="d-flex align-center flex-wrap gap-2 mb-3">
      <span class="text-caption text-medium-emphasis">
        共 {{ bots.bots.length }} 个账号，已连接 {{ bots.connectedCount }} 个
      </span>
      <v-spacer />
      <v-btn
        size="small"
        variant="tonal"
        prepend-icon="mdi-plug"
        :disabled="!!batchBusy"
        @click="batchAct('connect')"
      >
        {{ batchBusy === 'connect' ? `连接中 ${batchProgress.done}/${batchProgress.total}` : '全部连接' }}
      </v-btn>
      <v-btn
        size="small"
        variant="tonal"
        prepend-icon="mdi-unlink"
        :disabled="!!batchBusy"
        @click="batchAct('disconnect')"
      >
        {{ batchBusy === 'disconnect' ? `断开中 ${batchProgress.done}/${batchProgress.total}` : '全部断开' }}
      </v-btn>
    </div>

    <v-alert v-if="loadError" type="error" variant="tonal" class="mb-3">
      <div class="d-flex align-center flex-wrap gap-2">
        <span>账号列表加载失败：{{ loadError }}</span>
        <v-spacer />
        <v-btn size="small" variant="tonal" prepend-icon="mdi-refresh" @click="loadCards">重试</v-btn>
      </div>
    </v-alert>

    <v-row v-if="loadingCards && bots.bots.length === 0">
      <v-col v-for="i in 3" :key="i" cols="12" md="6" xl="4">
        <v-skeleton-loader type="card-avatar, article, actions" class="rounded-xl" />
      </v-col>
    </v-row>

    <v-card v-else-if="bots.bots.length === 0 && !loadError" variant="outlined">
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
              <v-chip size="x-small" :color="statusChipColor(b)" variant="tonal">
                <v-icon start size="x-small" icon="mdi-circle" /> {{ statusText(b) }}
              </v-chip>
              <span v-if="resolveAccount(b)?.nickname" class="text-caption app-line-clamp-1">
                {{ resolveAccount(b)!.nickname }}<template v-if="Number(resolveAccount(b)!.user_id) > 0"> ({{ resolveAccount(b)!.user_id }})</template>
              </span>
            </v-card-subtitle>
            <template #append>
              <v-btn
                variant="text"
                icon="mdi-stethoscope"
                color="primary"
                size="small"
                title="诊断这个账号（看协议端有没有送来消息）"
                @click="openDiagnostics(b)"
              />
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
              @update:model-value="(v: string) => { draftOf(b.id).name = v; markDirty() }"
              @blur="scheduleSave()"
            />
            <v-text-field
              :model-value="draftOf(b.id).ws_url"
              :label="draftOf(b.id).conn_mode === 'reverse' ? '本机监听地址' : '协议端地址'"
              :placeholder="draftOf(b.id).conn_mode === 'reverse' ? 'ws://0.0.0.0:3001/onebot/v11/ws' : 'ws://127.0.0.1:3001/'"
              density="comfortable"
              hide-details="auto"
              class="mb-3"
              @update:model-value="(v: string) => { draftOf(b.id).ws_url = v; markDirty() }"
              @blur="scheduleSave()"
            >
              <template #append-inner>
                <v-btn
                  icon="mdi-content-copy"
                  size="x-small"
                  variant="text"
                  title="复制地址"
                  @click="copyAddress(draftOf(b.id).ws_url)"
                />
              </template>
            </v-text-field>

            <v-btn
              size="small"
              variant="text"
              color="primary"
              class="protocol-help-toggle px-0 mb-2"
              :prepend-icon="helpOpen[b.id] ? 'mdi-chevron-up' : 'mdi-help-circle-outline'"
              @click="toggleHelp(b.id)"
            >
              协议端怎么填
            </v-btn>
            <v-expand-transition>
              <div v-if="helpOpen[b.id]" class="protocol-help mb-3">
                <template v-if="draftOf(b.id).conn_mode === 'reverse'">
                  <div>在 NapCat 或 LLOneBot 里新建「WebSocket 客户端」。</div>
                  <div>URL 填这张卡片上的本机监听地址，右侧图标可复制。</div>
                  <div>保存时勾选启用；协议端连上后这里会显示「已连接」。</div>
                  <div>127.0.0.1 只本机可用；协议端在另一台机器上要填本机 IP。</div>
                </template>
                <template v-else>
                  <div>在协议端里新建「WebSocket 服务端」。</div>
                  <div>把它的 ws 地址填进来，端口与路径要和协议端一致。</div>
                  <div>填好后点「连接」，协议端连上后这里会显示「已连接」。</div>
                </template>
              </div>
            </v-expand-transition>

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
              @update:model-value="(v: string) => { draftOf(b.id).access_token = v; markDirty() }"
              @blur="scheduleSave()"
            />
            <v-switch
              :model-value="draftOf(b.id).auto_connect"
              label="启动时自动连接"
              color="primary"
              density="compact"
              hide-details
              @update:model-value="(v: boolean | null) => { draftOf(b.id).auto_connect = !!v; markDirty() }"
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

    <!-- 诊断台：查这个账号最近收到的协议端事件，确认问题出在哪一侧 -->
    <EventMonitorDialog v-model="diagOpen" :account-id="diagAccountId" :account-name="diagAccountName" />
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

/* 协议端指引：折叠面板，四行短句，不做长篇文档 */
.protocol-help {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 12px;
  border-radius: 10px;
  background: rgba(var(--v-theme-primary), 0.06);
  font-size: 12.5px;
  line-height: 1.5;
  color: rgba(var(--v-theme-on-surface), 0.75);
}
</style>
