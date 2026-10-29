<script setup lang="ts">
/**
 * 「回复与权限」——把群级规则与原权限管理收进同一页，三个页签：
 *
 * - 群与私聊：简单视图（同步群列表 → 逐群三态开关 → 批量 / 静音 / 行内高级规则）。
 *   三态而不是布尔，是为了让「继承账号默认」这件事可见：否则用户关掉一个群后
 *   看不出它是「显式关闭」还是「本来就没开」。
 * - 谁能回复：按账号的响应权限档位、拥有者/管理者名册、高危工具审批
 *   （字段分组来自 `src/agent/ai-panel.json::reply.groups`，见 ReplyPermissionPanel）。
 * - 高级规则：保留账号默认规则的弹窗入口；逐群规则仍从群列表行内菜单打开。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { displayName, useBotsStore } from '@/stores/bots'
import { usePolicyStore, type PolicyMatrixRow, type PolicyOverrideRow, type SwitchValue } from '@/stores/policy'
import { useNotifyStore } from '@/stores/notify'
import { errorMessage } from '@/api/http'
import EmptyState from '@/components/EmptyState.vue'
import PolicyRulesDialog from '@/components/policy/PolicyRulesDialog.vue'
import ReplyPermissionPanel from '@/components/policy/ReplyPermissionPanel.vue'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'

const route = useRoute()
const router = useRouter()
const bots = useBotsStore()
const policy = usePolicyStore()
const notify = useNotifyStore()

// ---------- 页签（支持 ?tab=groups|who|rules，默认 groups） ----------
type PolicyTab = 'groups' | 'who' | 'rules'
const POLICY_TABS: PolicyTab[] = ['groups', 'who', 'rules']

function readTab(): PolicyTab {
  const raw = String(route.query.tab || '')
  return (POLICY_TABS as string[]).includes(raw) ? (raw as PolicyTab) : 'groups'
}

const tab = ref<PolicyTab>(readTab())

watch(tab, (value) => {
  // replace：切页签不新增历史记录，但刷新/分享能回到同一页签
  if (String(route.query.tab || '') !== value) {
    void router.replace({ query: { ...route.query, tab: value } })
  }
  // 从别的页签回到群列表时补一次加载（本页首次进入可能是「谁能回复」）
  if (value === 'groups' && !policy.rows.length && bots.currentAccountId) void loadWithGroups()
  // 高级规则 tab 每次进入都刷新覆盖摘要（弹窗来源相同，保证看到最新值）
  if (value === 'rules') void loadAccountOverride()
})

// 外部改地址（浏览器前进/后退、手改 query）时同步回页签
watch(
  () => route.query.tab,
  () => {
    tab.value = readTab()
  },
)

// 高级规则对话框：null = 账号默认层；否则是某个群
const rulesDialog = ref(false)
const rulesChannel = ref<string | null>(null)
const rulesTitle = ref('')

/** 账号级覆盖行（高级规则 tab 的摘要来源）：null = 还没读到 */
const accountOverride = ref<PolicyOverrideRow | null>(null)
const loadingAccountOverride = ref(false)

/**
 * 读账号层的覆盖行。
 *
 * 高级规则 tab 以前只有一句说明和一个按钮，用户看不到"我到底覆盖了什么"；
 * 这里把已覆盖项与值列出来，规则的真实编辑仍走弹窗。
 */
async function loadAccountOverride() {
  if (!bots.currentAccountId) {
    accountOverride.value = null
    return
  }
  loadingAccountOverride.value = true
  try {
    const detail = await policy.getPolicy(null)
    accountOverride.value = detail?.overrides?.account || null
  } catch {
    accountOverride.value = null
  } finally {
    loadingAccountOverride.value = false
  }
}

/** 覆盖项 → 人话摘要 */
const accountOverrideRows = computed<{ label: string; value: string }[]>(() => {
  const row = accountOverride.value
  const config = (row?.config || {}) as Record<string, any>
  const permission = (row?.permission || {}) as Record<string, any>
  const rows: { label: string; value: string }[] = []

  const hasAt = Object.prototype.hasOwnProperty.call(config, 'trigger_at')
  const hasKeyword = Object.prototype.hasOwnProperty.call(config, 'trigger_keyword')
  if (hasAt || hasKeyword) {
    const at = Boolean(config.trigger_at)
    const keywords = Array.isArray(config.trigger_keyword)
      ? config.trigger_keyword.map((item: any) => String(item)).filter(Boolean)
      : []
    if (at && keywords.length) rows.push({ label: '触发方式', value: `@ 或关键词（${keywords.join('、')}）` })
    else if (at) rows.push({ label: '触发方式', value: '仅 @' })
    else if (keywords.length) rows.push({ label: '触发方式', value: `仅关键词（${keywords.join('、')}）` })
    else rows.push({ label: '触发方式', value: '不触发' })
  }
  if (config.reply_cooldown !== undefined) {
    rows.push({ label: '回复冷却', value: `${config.reply_cooldown} 秒` })
  }
  if (config.message_debounce_seconds !== undefined) {
    rows.push({ label: '消息合并等待', value: `${config.message_debounce_seconds} 秒` })
  }
  if (config.quiet_hours) {
    rows.push({ label: '免打扰时段', value: String(config.quiet_hours) })
  }
  const mode = String(permission.user_mode || '')
  const userList = Array.isArray(permission.user_list) ? permission.user_list : []
  if (mode === 'blacklist') rows.push({ label: '用户例外', value: `黑名单 ${userList.length} 人` })
  if (mode === 'whitelist') rows.push({ label: '用户例外', value: `仅白名单 ${userList.length} 人` })
  if (row?.enabled === true) rows.push({ label: '账号开关', value: '一律允许' })
  if (row?.enabled === false) rows.push({ label: '账号开关', value: '一律不允许' })
  return rows
})

function openRules(channelId: string | null, title = '') {
  rulesChannel.value = channelId
  rulesTitle.value = title
  rulesDialog.value = true
}

const SWITCH_ITEMS: { title: string; value: SwitchValue }[] = [
  { title: '允许', value: 'on' },
  { title: '禁止', value: 'off' },
  { title: '继承', value: 'inherit' },
]

/** 账号默认（场景层）三态：跟随 AI 对话面板 / 一律允许 / 一律不允许 */
type SceneValue = 'on' | 'off' | 'inherit'
const GROUP_SCENE_ITEMS: { title: string; value: SceneValue }[] = [
  { title: '跟随 AI 对话面板（群聊开关）', value: 'inherit' },
  { title: '一律允许', value: 'on' },
  { title: '一律不允许', value: 'off' },
]
const PRIVATE_SCENE_ITEMS: { title: string; value: SceneValue }[] = [
  { title: '跟随 AI 对话面板（私信开关）', value: 'inherit' },
  { title: '一律允许', value: 'on' },
  { title: '一律不允许', value: 'off' },
]

const accountName = computed(() => {
  const bot = bots.bots.find((item) => item.id === bots.currentAccountId)
  return bot ? displayName(bot) : bots.currentAccountId
})

/** 群聊默认：群行选「继承」时用它；null = 跟随 AI 对话面板的群聊开关 */
const groupScene = computed<SceneValue>(() => {
  const value = policy.defaultPolicy.scene_enabled
  if (value === true) return 'on'
  if (value === false) return 'off'
  return 'inherit'
})

/**
 * 私聊默认：后端矩阵只返回群聊场景的显式值，私聊场景用 /api/policy 的解析结果反推——
 * enabled_source 为 scene 才是账号默认里显式设过，否则就是跟随面板。
 */
const privateScene = ref<SceneValue>('inherit')

async function loadPrivateScene() {
  try {
    const detail = await policy.getPolicy(null)
    if (!detail) {
      privateScene.value = 'inherit'
      return
    }
    privateScene.value =
      detail.resolved.enabled_source === 'scene' ? (detail.resolved.enabled ? 'on' : 'off') : 'inherit'
  } catch {
    privateScene.value = 'inherit'
  }
}

function sceneValueLabel(value: SceneValue): string {
  return value === 'on' ? '一律允许' : value === 'off' ? '一律不允许' : '跟随 AI 对话面板'
}

async function onSceneDefault(scene: 'group' | 'private', value: SceneValue) {
  try {
    await policy.setSceneEnabled(scene, value === 'inherit' ? null : value === 'on')
    if (scene === 'private') privateScene.value = value
    const label = scene === 'group' ? '群聊默认' : '私聊默认'
    notify.push(`${label}已设为「${sceneValueLabel(value)}」`, 'success')
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

function isMuted(row: PolicyMatrixRow): boolean {
  return row.muted_until > Date.now() / 1000
}

function mutedText(row: PolicyMatrixRow): string {
  return new Date(row.muted_until * 1000).toLocaleString('zh-CN', { hour12: false })
}

async function onSwitch(row: PolicyMatrixRow, value: SwitchValue) {
  try {
    await policy.setSwitch(row, value)
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

async function onRefreshGroups() {
  try {
    const count = await policy.refreshGroups()
    notify.push(`已同步 ${count} 个群`, 'success')
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

/** 批量开关：一次改动会影响已同步的全部群，先列影响清单再执行 */
const bulkTarget = ref<boolean | null>(null)
const bulkDialogOpen = ref(false)
const bulkLoading = ref(false)
const bulkTitle = computed(() =>
  bulkTarget.value === false ? '全部关闭群聊' : '全部允许群聊',
)
const bulkDetails = computed(() => [
  `将影响已同步的 ${policy.rows.length} 个群`,
  '只改本账号，不影响其它账号',
  '之后可以逐群再改回来',
])

function askBulk(enabled: boolean) {
  bulkTarget.value = enabled
  bulkDialogOpen.value = true
}

async function confirmBulk() {
  const enabled = bulkTarget.value
  if (enabled === null || bulkLoading.value) return
  const label = enabled ? '允许' : '关闭'
  bulkLoading.value = true
  try {
    await policy.bulk(enabled)
    notify.push(`已${label}全部群聊`, 'success')
    bulkDialogOpen.value = false
    bulkTarget.value = null
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    bulkLoading.value = false
  }
}

async function onMute(row: PolicyMatrixRow, minutes: number) {
  try {
    await policy.mute(row, minutes)
    notify.push(minutes > 0 ? `已静音 ${minutes} 分钟` : '已取消静音', 'success')
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

function onRestore(row: PolicyMatrixRow) {
  void onSwitch(row, 'inherit')
}

// 进入本页就拉一次群列表：否则只显示"上次已知"的群，新建/新加入的群看不到
async function loadWithGroups() {
  try {
    await policy.refreshGroups()
  } catch {
    // 账号未连接 / 协议端离线时刷新会失败：退回展示已缓存的群列表
    await policy.load()
  }
}

onMounted(async () => {
  if (!bots.bots.length) {
    // 账号列表加载失败不该冒成 unhandled rejection（全局会弹「页面出现异常」）
    try {
      await bots.fetchBots()
    } catch (err) {
      notify.push(`账号列表加载失败：${errorMessage(err)}`, 'error')
    }
  }
  // 首屏在「谁能回复」时只需矩阵数据，不必尝试同步群列表
  if (tab.value === 'groups') await loadWithGroups()
  else await policy.load()
  void loadPrivateScene()
  void loadAccountOverride()
})

watch(
  () => bots.currentAccountId,
  () => {
    void loadWithGroups()
    void loadPrivateScene()
    void loadAccountOverride()
  },
)

// 关闭规则弹窗后刷新摘要：保存成功的覆盖项要立刻出现在 tab 里
watch(rulesDialog, (open) => {
  if (!open) void loadAccountOverride()
})
</script>

<template>
  <div>
    <div class="app-page-header" style="align-items: center">
      <div>
        <h1 class="app-page-title">
          <v-icon icon="mdi-shield-account-outline" size="28" color="success" class="mr-1" />
          回复与权限
        </h1>
        <div class="app-page-subtitle">哪些群能回、谁能触发；AI 对话面板管默认，这里管例外</div>
      </div>
      <div class="d-flex align-center gap-2">
        <v-chip v-if="bots.currentAccountId" size="small" variant="tonal">
          账号：{{ accountName }}
        </v-chip>
        <v-btn
          v-if="tab === 'groups'"
          variant="tonal"
          prepend-icon="mdi-refresh"
          :loading="policy.refreshing"
          :disabled="!bots.currentAccountId"
          @click="onRefreshGroups"
        >
          同步群列表
        </v-btn>
      </div>
    </div>

    <v-tabs v-model="tab" density="compact" class="mb-3">
      <v-tab value="groups">群与私聊</v-tab>
      <v-tab value="who">谁能回复</v-tab>
      <v-tab value="rules">高级规则</v-tab>
    </v-tabs>

    <v-window v-model="tab">
      <v-window-item value="groups">
        <v-alert v-if="!bots.currentAccountId" type="warning" variant="tonal" density="compact" class="mb-3">
          <div class="d-flex align-center flex-wrap gap-2">
            <span class="text-caption">还没有账号：先添加一个 QQ 账号，再配置哪些群允许聊天。</span>
            <v-btn size="small" variant="text" prepend-icon="mdi-plus" @click="router.push('/bots')">
              去添加账号
            </v-btn>
          </div>
        </v-alert>

        <v-alert v-if="policy.error" type="error" variant="tonal" density="compact" class="mb-3">
          <div class="d-flex align-center flex-wrap gap-2">
            <span class="text-caption">{{ policy.error }}</span>
            <v-btn size="small" variant="text" prepend-icon="mdi-refresh" @click="onRefreshGroups">重试</v-btn>
          </div>
        </v-alert>

        <v-card variant="outlined" class="mb-3">
          <v-card-text class="d-flex align-center flex-wrap gap-3">
            <v-text-field
              v-model="policy.search"
              placeholder="搜索群名 / 群号"
              density="compact"
              variant="outlined"
              hide-details
              clearable
              prepend-inner-icon="mdi-magnify"
              style="max-width: 320px"
            />
            <v-chip size="small" variant="tonal">允许 {{ policy.allowedCount }} 个</v-chip>
            <v-chip v-if="policy.mutedCount" size="small" variant="tonal" color="warning">
              静音中 {{ policy.mutedCount }} 个
            </v-chip>
            <v-spacer />
            <v-btn
              size="small"
              variant="tonal"
              color="success"
              :disabled="!policy.rows.length || policy.saving !== null"
              @click="askBulk(true)"
            >
              全部允许
            </v-btn>
            <v-btn
              size="small"
              variant="tonal"
              color="error"
              :disabled="!policy.rows.length || policy.saving !== null"
              @click="askBulk(false)"
            >
              全部关闭
            </v-btn>
          </v-card-text>
        </v-card>

        <!-- 账号默认：群聊/私聊各自的三态；没单独设置的会话按这里的默认走 -->
        <v-card variant="outlined" class="mb-3">
          <v-card-title class="d-flex align-center">
            <v-icon icon="mdi-tune-variant" class="mr-2" color="primary" /> 账号默认
          </v-card-title>
          <v-card-text class="text-caption pt-0 pb-0" style="opacity: 0.7">
            没有单独设置的群或私聊按这里的默认走；单独设置过的以那一处为准。
          </v-card-text>
          <v-card-text>
            <v-row dense>
              <v-col cols="12" md="6">
                <v-select
                  :model-value="groupScene"
                  :items="GROUP_SCENE_ITEMS"
                  label="群聊默认"
                  density="compact"
                  variant="outlined"
                  hide-details
                  :loading="policy.savingScene"
                  :disabled="!bots.currentAccountId || policy.savingScene"
                  @update:model-value="(v: SceneValue) => onSceneDefault('group', v)"
                />
              </v-col>
              <v-col cols="12" md="6">
                <v-select
                  :model-value="privateScene"
                  :items="PRIVATE_SCENE_ITEMS"
                  label="私聊默认"
                  density="compact"
                  variant="outlined"
                  hide-details
                  :loading="policy.savingScene"
                  :disabled="!bots.currentAccountId || policy.savingScene"
                  @update:model-value="(v: SceneValue) => onSceneDefault('private', v)"
                />
              </v-col>
            </v-row>
            <div class="text-caption mt-2" style="opacity: 0.6">
              「跟随」指采用 AI 对话面板里的群聊开关、私信开关。
            </div>
          </v-card-text>
        </v-card>

        <v-progress-linear v-if="policy.loading" indeterminate color="primary" class="mb-3" />

        <v-card v-if="!policy.rows.length && !policy.loading" variant="outlined">
          <EmptyState
            icon="mdi-account-group-outline"
            title="还没有群列表"
            :description="
              bots.currentAccountId
                ? '账号连上后，点「同步群列表」就能逐群设置是否允许聊天。'
                : '先添加并连接一个 QQ 账号。'
            "
          >
            <v-btn
              v-if="bots.currentAccountId"
              color="primary"
              variant="tonal"
              prepend-icon="mdi-refresh"
              :loading="policy.refreshing"
              @click="onRefreshGroups"
            >
              同步群列表
            </v-btn>
            <v-btn v-else color="primary" variant="tonal" prepend-icon="mdi-plus" @click="router.push('/bots')">
              去添加账号
            </v-btn>
          </EmptyState>
        </v-card>

        <v-card v-else variant="outlined">
          <v-list density="compact" lines="two">
            <v-list-item
              v-for="row in policy.filtered"
              :key="row.group_id"
              :class="{ 'policy-row--muted': isMuted(row) }"
            >
              <template #prepend>
                <v-icon
                  :icon="row.enabled ? 'mdi-check-circle-outline' : 'mdi-pause-circle-outline'"
                  :color="isMuted(row) ? 'warning' : row.enabled ? 'success' : 'grey'"
                />
              </template>
              <v-list-item-title class="d-flex align-center flex-wrap gap-2">
                <span>{{ row.name }}</span>
                <v-chip size="x-small" variant="text">{{ row.group_id }}</v-chip>
                <v-chip v-if="row.member_count" size="x-small" variant="text">{{ row.member_count }} 人</v-chip>
                <v-chip v-if="!row.in_group" size="x-small" color="warning" variant="tonal">已退群</v-chip>
                <v-chip v-if="isMuted(row)" size="x-small" color="warning" variant="tonal">
                  静音至 {{ mutedText(row) }}
                </v-chip>
                <v-chip
                  v-else-if="row.overridden_keys.length"
                  size="x-small"
                  color="info"
                  variant="tonal"
                >
                  高级规则 {{ row.overridden_keys.length }} 项
                </v-chip>
              </v-list-item-title>
              <v-list-item-subtitle>
                {{ row.enabled_source === 'muted' ? '静音中' : row.enabled_source === 'channel' ? '按本群设置' : '继承账号默认' }}
              </v-list-item-subtitle>
              <template #append>
                <div class="d-flex align-center gap-2">
                  <v-select
                    :model-value="policy.switchValueOf(row)"
                    :items="SWITCH_ITEMS"
                    density="compact"
                    variant="outlined"
                    hide-details
                    style="max-width: 120px"
                    :loading="policy.saving === row.group_id"
                    :disabled="policy.saving !== null"
                    @update:model-value="(v: SwitchValue) => onSwitch(row, v)"
                  />
                  <v-menu location="bottom end">
                    <template #activator="{ props }">
                      <v-btn v-bind="props" icon="mdi-dots-vertical" variant="text" size="small" />
                    </template>
                    <v-list density="compact">
                      <v-list-item v-if="isMuted(row)" prepend-icon="mdi-bell-off-outline" @click="onMute(row, 0)">
                        取消静音
                      </v-list-item>
                      <template v-else>
                        <v-list-item prepend-icon="mdi-bell-sleep-outline" @click="onMute(row, 60)">
                          静音 1 小时
                        </v-list-item>
                        <v-list-item prepend-icon="mdi-bell-sleep-outline" @click="onMute(row, 1440)">
                          静音 24 小时
                        </v-list-item>
                      </template>
                      <v-list-item prepend-icon="mdi-undo-variant" @click="onRestore(row)">
                        恢复继承
                      </v-list-item>
                      <v-list-item
                        prepend-icon="mdi-tune"
                        @click="openRules(row.group_id, `本群专属规则 · ${row.name}`)"
                      >
                        本群专属规则
                      </v-list-item>
                    </v-list>
                  </v-menu>
                </div>
              </template>
            </v-list-item>
          </v-list>
        </v-card>

        <div v-if="policy.rows.length" class="text-caption mt-3" style="opacity: 0.6">
          群列表来自账号在线时的一次同步；离线时显示上次结果，可能不是最新。
        </div>
      </v-window-item>

      <v-window-item value="who">
        <ReplyPermissionPanel />
      </v-window-item>

      <v-window-item value="rules">
        <!-- 同群多账号的分工在插件层：这里给一个明确的去处，避免用户在此页反复猜 -->
        <v-card variant="outlined" class="mb-3">
          <v-card-text>
            <div class="d-flex align-center flex-wrap gap-2">
              <v-icon icon="mdi-account-multiple-outline" color="primary" class="mr-1" />
              <span class="text-body-2">
                同一个群装了多个账号时，由哪个账号处理某个插件，在「功能插件 → 对应插件 → 响应权策略」里配置
              </span>
              <v-spacer />
              <v-btn size="small" variant="tonal" prepend-icon="mdi-open-in-new" @click="router.push('/modules')">
                去功能插件
              </v-btn>
            </div>
          </v-card-text>
        </v-card>

        <!-- 当前账号级覆盖摘要：让用户先看到"我改过什么、现在值是多少"，再决定要不要进弹窗 -->
        <v-card variant="outlined" class="mb-3">
          <v-card-title class="d-flex align-center">
            <v-icon icon="mdi-format-list-checkbox" class="mr-2" color="primary" /> 当前账号专属规则
            <v-spacer />
            <v-chip v-if="accountOverrideRows.length" size="small" variant="tonal">
              已覆盖 {{ accountOverrideRows.length }} 项
            </v-chip>
          </v-card-title>
          <v-card-text>
            <v-progress-linear v-if="loadingAccountOverride" indeterminate color="primary" class="mb-2" />
            <v-list v-else-if="accountOverrideRows.length" density="compact">
              <v-list-item v-for="row in accountOverrideRows" :key="row.label">
                <v-list-item-title class="text-body-2">{{ row.label }}</v-list-item-title>
                <v-list-item-subtitle>{{ row.value }}</v-list-item-subtitle>
              </v-list-item>
            </v-list>
            <div v-else class="text-caption">
              还没有账号专属规则，全部按 AI 对话面板与账号默认执行。
            </div>
          </v-card-text>
        </v-card>

        <v-card variant="outlined">
          <v-card-title class="d-flex align-center">
            <v-icon icon="mdi-tune" class="mr-2" color="primary" /> 高级规则
          </v-card-title>
          <v-card-text>
            <div class="text-body-2 mb-3">
              触发方式、回复冷却、免打扰时段与用户例外可按「账号专属规则」和「单个群」两层设置；
              这里的设置优先于 AI 对话面板里的默认值。
            </div>
            <v-btn
              color="primary"
              variant="tonal"
              prepend-icon="mdi-cog-outline"
              :disabled="!bots.currentAccountId"
              @click="openRules(null, '账号专属规则')"
            >
              账号专属规则
            </v-btn>
            <div class="text-caption mt-3" style="opacity: 0.6">
              单个群的规则从「群与私聊」列表每行右侧菜单打开。
            </div>
          </v-card-text>
        </v-card>
      </v-window-item>
    </v-window>

    <PolicyRulesDialog v-model="rulesDialog" :channel-id="rulesChannel" :title="rulesTitle" />

    <ConfirmDialog
      v-model="bulkDialogOpen"
      :title="bulkTitle"
      :details="bulkDetails"
      danger
      confirm-text="确认"
      :loading="bulkLoading"
      @confirm="confirmBulk"
    />
  </div>
</template>

<style scoped>
.policy-row--muted {
  opacity: 0.7;
}
</style>
