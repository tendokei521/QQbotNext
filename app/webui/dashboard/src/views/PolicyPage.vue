<script setup lang="ts">
/**
 * 「权限与回复」——简单视图（群聊开关矩阵）。
 *
 * 设计目标（docs/permission-dialogue-policy-design.md §6.2）：
 * 小白只需要回答一个问题——**哪个群允许聊天**。因此这一页不出现"档位/名单/
 * 触发方式"等高级项，只保留：拉取群列表 → 逐群三态开关（允许/禁止/继承）→ 批量。
 *
 * 三态而不是布尔，是为了让"继承账号默认"这件事可见：否则用户关掉一个群后
 * 看不出它是"显式关闭"还是"本来就没开"。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { displayName, useBotsStore } from '@/stores/bots'
import { usePolicyStore, type PolicyMatrixRow, type SwitchValue } from '@/stores/policy'
import { useNotifyStore } from '@/stores/notify'
import { errorMessage } from '@/api/http'
import EmptyState from '@/components/EmptyState.vue'
import PolicyRulesDialog from '@/components/policy/PolicyRulesDialog.vue'

const router = useRouter()
const bots = useBotsStore()
const policy = usePolicyStore()
const notify = useNotifyStore()

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

/** 「账号默认（群聊）」三态：允许 / 不允许 / 跟随 Agent 配置 */
type SceneValue = 'on' | 'off' | 'inherit'
const SCENE_ITEMS: { title: string; value: SceneValue }[] = [
  { title: '允许', value: 'on' },
  { title: '不允许', value: 'off' },
  { title: '跟随 Agent 配置', value: 'inherit' },
]

const accountName = computed(() => {
  const bot = bots.bots.find((item) => item.id === bots.currentAccountId)
  return bot ? displayName(bot) : bots.currentAccountId
})

/** 群行选「继承」时用得就是这个值；null = 跟随 Agent 配置里的「群回复启用」 */
const sceneDefault = computed<SceneValue>(() => {
  const value = policy.defaultPolicy.scene_enabled
  if (value === true) return 'on'
  if (value === false) return 'off'
  return 'inherit'
})

async function onSceneDefault(value: SceneValue) {
  try {
    await policy.setSceneEnabled(value === 'inherit' ? null : value === 'on')
    notify.push(
      value === 'inherit' ? '已改为跟随 Agent 配置' : `账号默认已设为「${value === 'on' ? '允许' : '不允许'}」`,
      'success',
    )
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
    notify.push(`已拉取 ${count} 个群`, 'success')
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

async function onBulk(enabled: boolean) {
  const label = enabled ? '允许' : '禁止'
  if (!window.confirm(`将把 ${policy.rows.length} 个群设为「${label}」，是否继续？`)) return
  try {
    await policy.bulk(enabled)
    notify.push(`已${label}全部群聊`, 'success')
  } catch (err) {
    notify.push(errorMessage(err), 'error')
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
  if (!bots.bots.length) await bots.fetchBots()
  await loadWithGroups()
})

watch(
  () => bots.currentAccountId,
  () => {
    void loadWithGroups()
  },
)
</script>

<template>
  <div>
    <div class="app-page-header" style="align-items: center">
      <div>
        <h1 class="app-page-title">
          <v-icon icon="mdi-shield-account-outline" size="28" color="success" class="mr-1" />
          权限与回复
        </h1>
        <div class="app-page-subtitle">哪些群允许聊天、默认允许还是不允许</div>
      </div>
      <div class="d-flex align-center gap-2">
        <v-chip v-if="bots.currentAccountId" size="small" variant="tonal">
          账号：{{ accountName }}
        </v-chip>
        <v-btn
          variant="tonal"
          prepend-icon="mdi-refresh"
          :loading="policy.refreshing"
          :disabled="!bots.currentAccountId"
          @click="onRefreshGroups"
        >
          拉取群列表
        </v-btn>
        <v-btn variant="text" prepend-icon="mdi-tune" @click="router.push('/agent/permission')">
          高级规则
        </v-btn>
        <v-btn
          variant="tonal"
          prepend-icon="mdi-cog-outline"
          :disabled="!bots.currentAccountId"
          @click="openRules(null, '账号默认规则')"
        >
          默认规则
        </v-btn>
      </div>
    </div>

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
        <v-select
          :model-value="sceneDefault"
          :items="SCENE_ITEMS"
          label="账号默认（群聊）"
          density="compact"
          variant="outlined"
          hide-details
          style="max-width: 200px"
          :loading="policy.savingScene"
          :disabled="!bots.currentAccountId || policy.savingScene"
          @update:model-value="(v: SceneValue) => onSceneDefault(v)"
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
          @click="onBulk(true)"
        >
          全部允许
        </v-btn>
        <v-btn
          size="small"
          variant="tonal"
          color="error"
          :disabled="!policy.rows.length || policy.saving !== null"
          @click="onBulk(false)"
        >
          全部关闭
        </v-btn>
      </v-card-text>
    </v-card>

    <v-progress-linear v-if="policy.loading" indeterminate color="primary" class="mb-3" />

    <v-card v-if="!policy.rows.length && !policy.loading" variant="outlined">
      <EmptyState
        icon="mdi-account-group-outline"
        title="还没有群列表"
        :description="
          bots.currentAccountId
            ? '账号连接成功后点「拉取群列表」，就能逐群设置是否允许聊天。'
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
          拉取群列表
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
                    @click="openRules(row.group_id, `高级规则 · ${row.name}`)"
                  >
                    本群高级规则
                  </v-list-item>
                </v-list>
              </v-menu>
            </div>
          </template>
        </v-list-item>
      </v-list>
    </v-card>

    <PolicyRulesDialog v-model="rulesDialog" :channel-id="rulesChannel" :title="rulesTitle" />

    <div v-if="policy.rows.length" class="text-caption mt-3" style="opacity: 0.6">
      群列表来自账号在线时的一次拉取；离线时显示上次结果，可能不是最新。
    </div>
  </div>
</template>

<style scoped>
.policy-row--muted {
  opacity: 0.7;
}
</style>
