<script setup lang="ts">
/**
 * 「回复与权限」——把群级规则与原权限管理收进同一页，三个页签：
 *
 * - 群与私聊：简单视图（同步群列表 → 逐群三态开关 → 批量 / 静音 / 行内高级规则）。
 *   三态而不是布尔，是为了让「继承账号默认」这件事可见：否则用户关掉一个群后
 *   看不出它是「显式关闭」还是「本来就没开」。
 * - 谁能回复：按账号的响应权限档位、拥有者/管理者名册、高危工具审批
 *   （字段分组来自 `src/agent/ai-panel.json::reply.groups`，见 ReplyPermissionPanel）。
 */
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useBotsStore } from '@/stores/bots'
import {
  triggerModeLabel,
  usePolicyStore,
  type PolicyDecision,
  type PolicyDecisionCheck,
  type PolicyDecisionResult,
  type PolicyMatrixRow,
  type SwitchValue,
} from '@/stores/policy'
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

// ---------- 页签（支持 ?tab=groups|who，默认 groups） ----------
type PolicyTab = 'groups' | 'who'
const POLICY_TABS: PolicyTab[] = ['groups', 'who']

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

function triggerText(row: PolicyMatrixRow): string {
  const label = triggerModeLabel(row.trigger_mode)
  return row.trigger_mode === 'all' ? `${label} ${Number(row.trigger_probability ?? 100)}%` : label
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

// ---------- 判定链（「谁能回复」页签顶部） ----------
/** 身份档位：后端 roles 的展示名，账号级 / 群级共用 */
const ROLE_LABELS: Record<string, string> = {
  everyone: '所有人',
  member: '普通成员',
  group_admin: '群管理员',
  group_owner: '群主',
  manager: '管理者',
  owner: '拥有者',
}

function tierLabel(role?: string | null): string {
  if (!role) return '不限'
  return ROLE_LABELS[role] || role
}

/** 判定链来源 → 用户看得懂的位置名 */
const SOURCE_LABELS: Record<string, string> = {
  default: '默认',
  account: '账号默认',
  scene: '场景默认',
  channel: '该群',
  muted: '静音',
  agent_config: 'Agent 配置',
  module_authority: '模块权限',
  module: '模块声明',
  single_service: '单一服务',
  responder: '多账号仲裁',
  all: '所有账号',
  global: '全局',
  role: '按身份',
}

function sourceLabel(source?: string | null): string {
  if (!source) return '默认'
  return SOURCE_LABELS[source] || source
}

const decisionChannel = ref<string>('')
const decisionUserId = ref('')
const decisionRole = ref<string>('')
const decisionLoading = ref(false)
const decisionError = ref('')
const decisionResult = ref<PolicyDecisionResult | null>(null)
const decision = computed<PolicyDecision | null>(() => decisionResult.value?.decision || null)

const accountDefaultEl = ref<HTMLElement | null>(null)
const permissionPanelEl = ref<HTMLElement | null>(null)

const channelOptions = computed(() => [
  { title: '私聊 / 不限', value: '' },
  ...policy.rows.map((row) => ({ title: `${row.name}（${row.group_id}）`, value: row.group_id })),
])

const roleOptions = computed(() => [
  { title: '不评估', value: '' },
  ...(decisionResult.value?.roles || Object.keys(ROLE_LABELS)).map((role) => ({
    title: tierLabel(role),
    value: role,
  })),
])

async function runDecision() {
  if (!bots.currentAccountId) {
    notify.push('请先选择一个账号', 'warning')
    return
  }
  decisionLoading.value = true
  decisionError.value = ''
  try {
    decisionResult.value = await policy.fetchDecision({
      channelId: decisionChannel.value || null,
      userId: decisionUserId.value,
      role: decisionRole.value || null,
    })
  } catch (err) {
    decisionError.value = errorMessage(err)
    decisionResult.value = null
  } finally {
    decisionLoading.value = false
  }
}

/** 「跳到实际生效的那一层」：只在本页切页签/滚动/开本群规则，不跳新页面 */
interface JumpAction {
  label: string
  target: 'group' | 'account' | 'permission'
}

function jumpsFor(state: PolicyDecision | null): JumpAction[] {
  if (!state) return []
  const jumps: JumpAction[] = []
  const add = (target: JumpAction['target'], label: string) => {
    if (!jumps.some((item) => item.target === target)) jumps.push({ target, label })
  }
  const decidedBy = String(state.decided_by || '')
  if (decidedBy === 'channel' || decidedBy === 'muted') add('group', '打开该群规则')
  else if (decidedBy === 'account' || decidedBy === 'scene' || decidedBy === 'default') add('account', '去账号默认')
  else if (decidedBy) add('permission', '去身份档位')
  if (state.required_tier_source === 'channel') add('group', '去该群触发身份')
  else if (state.required_tier_source === 'account') add('permission', '去身份档位')
  return jumps.slice(0, 2)
}

async function doJump(jump: JumpAction) {
  if (jump.target === 'permission') {
    await nextTick()
    permissionPanelEl.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    return
  }
  tab.value = 'groups'
  await nextTick()
  if (jump.target === 'account') {
    accountDefaultEl.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    return
  }
  const state = decision.value
  if (state?.channel_id) {
    const row = policy.rows.find((item) => item.group_id === state.channel_id)
    openRules(state.channel_id, `本群专属规则 · ${row?.name || state.channel_id}`)
  }
}

/** 每一层右侧的「去哪改」快捷入口 */
function checkJump(check: PolicyDecisionCheck): JumpAction | null {
  if (check.source === 'channel') return { label: '该群规则', target: 'group' }
  if (check.source === 'account' || check.source === 'scene' || check.source === 'default') {
    return { label: '账号默认', target: 'account' }
  }
  if (check.source === 'agent_config') return { label: '身份档位', target: 'permission' }
  return null
}

function checkJumpLabel(check: PolicyDecisionCheck): string {
  return checkJump(check)?.label || ''
}

function onCheckJump(check: PolicyDecisionCheck) {
  const jump = checkJump(check)
  if (jump) void doJump(jump)
}

/** 结论一句话 + 多账号响应权 / 身份要求，文案集中在这里，模板里只插值 */
const decisionConclusion = computed(() => {
  const state = decision.value
  if (!state) return ''
  return state.allowed ? '会回复' : `不会回复 · 被「${state.decided_by_label}」拦下`
})

const responderLine = computed(() => {
  const responder = decision.value?.responder
  if (!responder) return '未设置'
  const parts = [responder.policy || '未设置']
  if (responder.responders?.length) parts.push(`由 ${responder.responders.join('、')} 响应`)
  if (responder.candidates?.length) parts.push(`候选 ${responder.candidates.join('、')}`)
  if (responder.degraded) parts.push('已降级')
  return parts.join(' · ')
})

const tierLine = computed(() => {
  const state = decision.value
  if (!state) return ''
  const parts = [`身份要求：${tierLabel(state.required_tier)}（${sourceLabel(state.required_tier_source)}）`]
  if (state.actual_tier) parts.push(`实际：${tierLabel(state.actual_tier)}`)
  return parts.join(' · ')
})

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
})

watch(
  () => bots.currentAccountId,
  () => {
    void loadWithGroups()
    void loadPrivateScene()
  },
)

</script>

<template>
  <div>
    <div class="app-page-header">
      <div>
        <h1 class="app-page-title">
          <v-icon icon="mdi-shield-account-outline" size="28" color="success" class="mr-1" />
          回复与权限
        </h1>
        <div class="app-page-subtitle">哪些群能回、谁能触发</div>
      </div>
      <div class="d-flex align-center gap-2">
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

        <div class="app-toolbar">
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
          <span class="text-caption app-muted">允许 {{ policy.allowedCount }} 个</span>
          <span v-if="policy.mutedCount" class="text-caption app-muted">静音 {{ policy.mutedCount }} 个</span>
          <v-spacer />
          <v-btn
            size="small"
            variant="text"
            color="success"
            :disabled="!policy.rows.length || policy.saving !== null"
            @click="askBulk(true)"
          >
            全部允许
          </v-btn>
          <v-btn
            size="small"
            variant="text"
            color="error"
            :disabled="!policy.rows.length || policy.saving !== null"
            @click="askBulk(false)"
          >
            全部关闭
          </v-btn>
        </div>

        <!-- 账号默认：群聊/私聊各自的三态；没单独设置的会话按这里的默认走 -->
        <div ref="accountDefaultEl">
          <v-card variant="outlined" class="mb-4">
          <v-card-title class="d-flex align-center">
            <v-icon icon="mdi-tune-variant" class="mr-2" color="primary" /> 账号默认
          </v-card-title>
          <v-card-text>
            <div class="app-form-grid">
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
            </div>
            <div class="text-caption mt-2" style="opacity: 0.6">
              未单独设置的按这里，单独设置的优先
            </div>
          </v-card-text>
          </v-card>
        </div>

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
              <v-list-item-subtitle class="d-flex align-center flex-wrap gap-2">
                <span>
                  {{ row.enabled_source === 'muted' ? '静音中' : row.enabled_source === 'channel' ? '按本群设置' : '继承账号默认' }}
                </span>
                <span>· 触发：{{ triggerText(row) }}</span>
                <span v-if="row.quiet_hours">· 免打扰 {{ row.quiet_hours }}</span>
                <v-chip
                  v-if="row.trigger_source === 'channel'"
                  size="x-small"
                  variant="tonal"
                  color="info"
                >
                  触发按本群
                </v-chip>
              </v-list-item-subtitle>
              <template #append>
                <div class="d-flex align-center gap-2">
                  <v-select
                    :model-value="policy.switchValueOf(row)"
                    :items="SWITCH_ITEMS"
                    density="compact"
                    variant="outlined"
                    hide-details
                    class="row-switch"
                    :loading="policy.saving === row.group_id"
                    :disabled="policy.saving !== null"
                    @update:model-value="(v: SwitchValue) => onSwitch(row, v)"
                  />
                  <v-menu location="bottom end">
                    <template #activator="{ props }">
                      <v-btn v-bind="props" icon="mdi-dots-vertical" variant="text" size="small" class="row-more" />
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
          群列表来自上次同步，离线时可能不是最新。
        </div>
      </v-window-item>

      <v-window-item value="who">
        <v-card variant="outlined" class="mb-4">
          <v-card-title class="d-flex align-center flex-wrap gap-2">
            <v-icon icon="mdi-source-branch" class="mr-2" color="primary" />
            判定链
            <v-spacer />
            <v-btn
              size="small"
              color="primary"
              variant="tonal"
              prepend-icon="mdi-magnify"
              :loading="decisionLoading"
              :disabled="!bots.currentAccountId"
              @click="runDecision"
            >
              查看判定链
            </v-btn>
          </v-card-title>
          <v-card-text>
            <div class="app-form-grid">
              <v-select
                v-model="decisionChannel"
                :items="channelOptions"
                label="群聊"
                density="compact"
                variant="outlined"
                hide-details
              />
              <v-text-field
                v-model="decisionUserId"
                label="发信人 QQ"
                placeholder="可留空"
                density="compact"
                variant="outlined"
                hide-details
              />
              <v-select
                v-model="decisionRole"
                :items="roleOptions"
                label="身份档位"
                density="compact"
                variant="outlined"
                hide-details
              />
            </div>
            <div class="text-caption mt-2" style="opacity: 0.65">
              用当前账号预览一条消息的真实判定过程；留空的项不评估。
            </div>

            <v-alert v-if="decisionError" type="error" variant="tonal" density="compact" class="mt-3">
              {{ decisionError }}
            </v-alert>

            <template v-if="decision">
              <v-alert :type="decision.allowed ? 'success' : 'error'" variant="tonal" density="compact" class="mt-3">
                <div class="font-weight-medium">{{ decisionConclusion }}</div>
                <div v-if="decision.hint" class="text-caption mt-1">{{ decision.hint }}</div>
                <div v-if="jumpsFor(decision).length" class="d-flex flex-wrap gap-2 mt-2">
                  <v-btn
                    v-for="jump in jumpsFor(decision)"
                    :key="jump.label"
                    size="small"
                    variant="tonal"
                    append-icon="mdi-arrow-right"
                    @click="doJump(jump)"
                  >
                    {{ jump.label }}
                  </v-btn>
                </div>
              </v-alert>

              <div class="decision-checks mt-3">
                <div v-for="check in decision.checks" :key="check.stage" class="decision-row">
                  <v-icon
                    :icon="check.allowed ? 'mdi-check-circle-outline' : 'mdi-close-circle-outline'"
                    :color="check.allowed ? 'success' : 'error'"
                    size="18"
                  />
                  <span class="decision-stage">{{ check.label }}</span>
                  <v-chip size="x-small" :color="check.allowed ? 'success' : 'error'" variant="tonal">
                    {{ check.allowed ? '通过' : '拦截' }}
                  </v-chip>
                  <span class="text-caption app-muted">{{ sourceLabel(check.source) }}</span>
                  <span v-if="check.detail" class="text-caption decision-detail">{{ check.detail }}</span>
                  <v-btn
                    v-if="checkJumpLabel(check)"
                    size="x-small"
                    variant="text"
                    color="primary"
                    class="decision-jump"
                    @click="onCheckJump(check)"
                  >
                    {{ checkJumpLabel(check) }}
                  </v-btn>
                </div>
              </div>

              <div class="decision-extra mt-3">
                <div class="text-caption">响应权：{{ responderLine }}</div>
                <div class="text-caption">{{ tierLine }}</div>
              </div>
            </template>
          </v-card-text>
        </v-card>

        <div ref="permissionPanelEl">
          <ReplyPermissionPanel />
        </div>
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

.row-switch {
  width: 120px;
  flex: 0 0 120px;
}

.row-switch :deep(.v-field) {
  min-height: 40px;
}

.row-more {
  width: 40px;
  height: 40px;
}

/* 判定链：一层一行，窄屏自动换行 */
.decision-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  padding: 6px 0;
  border-bottom: 1px dashed rgba(var(--v-theme-on-surface), 0.07);
}

.decision-stage {
  font-size: 13px;
  font-weight: 500;
  min-width: 76px;
}

.decision-detail {
  flex: 1 1 220px;
  min-width: 0;
  opacity: 0.65;
}

.decision-jump {
  margin-left: auto;
}

.decision-extra {
  display: flex;
  flex-direction: column;
  gap: 4px;
  color: rgba(var(--v-theme-on-surface), 0.65);
}
</style>
