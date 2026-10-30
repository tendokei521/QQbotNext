<script setup lang="ts">
/**
 * 插件响应策略 · 单插件配置页（独立页，不挤在模块设置里）。
 *
 * 只回答一件事：**同一个群里装了多个账号时，这个插件由哪个账号处理**。
 * 与「回复与权限」的区别：那边管"能不能触发/谁能触发"，这里管"谁响应"，
 * 两层语义正交（docs/multi-account-design.md §6）。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useResponderStore, type ResponderGroup } from '@/stores/responder'
import { useBotsStore, displayName } from '@/stores/bots'
import { useNotifyStore } from '@/stores/notify'
import { errorMessage } from '@/api/http'

const route = useRoute()
const router = useRouter()
const responder = useResponderStore()
const bots = useBotsStore()
const notify = useNotifyStore()

const name = String(route.params.name || '')

const POLICY_ITEMS = [
  { title: '各自响应（默认）', value: 'all' },
  { title: '同一群只回一次', value: 'dedupe' },
  { title: '固定由某个账号响应', value: 'delegate' },
  { title: '主备：主号优先，失败换备用', value: 'leader' },
]

/** 仲裁原因 → 人话（后端给的是结构化 token） */
const REASON_LABELS: Record<string, string> = {
  all: '各自响应',
  dedupe: '去重后选一个',
  primary: '由指定账号响应',
  'no-candidates': '没有可用账号',
  'missing-primary': '未指定主账号',
  'primary-unavailable': '指定账号不可用',
}

function reasonLabel(reason?: string | null): string {
  const text = String(reason || '').trim()
  if (!text) return ''
  const [base, suffix] = text.split(':')
  const baseLabel = REASON_LABELS[base] || base
  if (suffix === 'fallback-open') return `${baseLabel}，已降级放行`
  if (suffix === 'closed') return `${baseLabel}，按配置不响应`
  return baseLabel
}

const data = computed(() => responder.dataOf(name))
const loading = computed(() => responder.loadingOf(name))
const saving = computed(() => responder.savingOf(name))
const dirty = ref(false)
const draft = ref<Record<string, { policy: string; primary_account_id: string | null; fallback: string }>>({})

const displayNameText = computed(() => data.value?.name || name)
const sign = computed(() => data.value?.sign || '')
const description = computed(() => data.value?.description || '')
/** 只有"同群 ≥2 个账号"的群才需要响应策略；单账号群配了也没意义 */
const rows = computed(() => (data.value?.groups || []).filter((group) => group.accounts.length >= 2))

function accountName(accountId: string): string {
  const bot = bots.bots.find((item) => item.id === accountId)
  return bot ? displayName(bot) : accountId
}

function needsPrimary(policy: string | undefined): boolean {
  return policy === 'delegate' || policy === 'leader'
}

function primaryItems(group: ResponderGroup) {
  return [
    { title: '未指定（按兜底方式）', value: null },
    ...group.accounts.map((accountId) => ({ title: accountName(accountId), value: accountId })),
  ]
}

function syncDraft() {
  const current = data.value
  if (!current) return
  const next: typeof draft.value = {}
  current.groups.forEach((group) => {
    next[group.group_id] = {
      policy: group.policy || 'all',
      primary_account_id: group.primary_account_id,
      fallback: group.fallback || 'open',
    }
  })
  draft.value = next
  dirty.value = false
}

async function load() {
  try {
    await responder.load(name)
    syncDraft()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

function onPolicyChange(groupId: string, policy: string) {
  const item = draft.value[groupId]
  if (!item) return
  item.policy = policy
  if (needsPrimary(policy) && !item.primary_account_id) {
    item.primary_account_id = rows.value.find((row) => row.group_id === groupId)?.accounts[0] ?? null
  }
  dirty.value = true
}

function onPrimaryChange(groupId: string, primary: string | null) {
  const item = draft.value[groupId]
  if (!item) return
  item.primary_account_id = primary || null
  dirty.value = true
}

function onFallbackChange(groupId: string, silent: boolean | null) {
  const item = draft.value[groupId]
  if (!item) return
  item.fallback = silent ? 'closed' : 'open'
  dirty.value = true
}

function respondersText(group: ResponderGroup): string {
  const preview = group.preview
  if (preview?.responders?.length) {
    return `当前由 ${preview.responders.map(accountName).join('、')} 响应`
  }
  if (preview?.reason) return `当前无人响应（${reasonLabel(preview.reason)}）`
  return '当前无人响应'
}

async function onSave() {
  const policies = Object.entries(draft.value).map(([groupId, item]) => ({
    group_id: groupId,
    policy: item.policy,
    primary_account_id: item.primary_account_id,
    fallback: item.fallback,
  }))
  try {
    const result = await responder.save(name, policies)
    dirty.value = false
    if (result.warnings.length) notify.push(result.warnings.join('；'), 'warning')
    else notify.push(result.message, 'success')
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

function goBack() {
  void router.push('/responder')
}

onMounted(load)
watch(() => bots.currentAccountId, () => void load())
</script>

<template>
  <div>
    <v-btn variant="text" size="small" prepend-icon="mdi-arrow-left" class="mb-2" @click="goBack">
      插件响应策略
    </v-btn>

    <div class="d-flex align-center flex-wrap gap-2">
      <h2 class="text-h5">{{ displayNameText }}</h2>
      <v-chip v-if="sign" size="x-small" variant="tonal">{{ sign }}</v-chip>
      <v-chip size="x-small" variant="tonal">同群多账号 · 对所有账号生效</v-chip>
    </div>
    <div v-if="description" class="app-page-subtitle">{{ description }}</div>
    <div class="app-page-subtitle">
      同一个群同时装了多个账号时，由哪个账号处理这个插件；只对声明支持多账号响应权的插件生效。
    </div>

    <v-card variant="outlined" class="mt-3">
      <v-card-title class="d-flex align-center flex-wrap gap-2">
        <v-icon icon="mdi-account-multiple-check" class="mr-2" color="primary" />
        群级响应策略
        <v-chip v-if="dirty" size="x-small" color="warning" variant="tonal">未保存</v-chip>
        <v-spacer />
        <v-btn
          size="small"
          color="primary"
          variant="tonal"
          prepend-icon="mdi-content-save"
          :loading="saving"
          :disabled="!dirty || loading"
          @click="onSave"
        >
          保存策略
        </v-btn>
      </v-card-title>
      <v-card-text>
        <div v-if="loading" class="text-caption">加载中…</div>
        <div v-else-if="!rows.length" class="text-caption">
          暂无可配置的群：需要同一个群里装了至少两个账号（群列表来自登录时缓存，账号离线也能看到）。
        </div>
        <div v-for="group in rows" :key="group.group_id" class="responder-row">
          <div class="responder-group-info">
            <div class="font-weight-medium">{{ group.name }}</div>
            <div class="text-caption">
              {{ group.group_id }} · {{ group.accounts.map(accountName).join('、') }}
            </div>
          </div>
          <v-select
            :model-value="draft[group.group_id]?.policy || 'all'"
            :items="POLICY_ITEMS"
            label="谁响应"
            density="compact"
            variant="outlined"
            hide-details
            class="responder-select"
            @update:model-value="(v: string) => onPolicyChange(group.group_id, v)"
          />
          <v-select
            :model-value="draft[group.group_id]?.primary_account_id ?? null"
            :items="primaryItems(group)"
            label="指定账号"
            :disabled="!needsPrimary(draft[group.group_id]?.policy)"
            density="compact"
            variant="outlined"
            hide-details
            class="responder-select"
            @update:model-value="(v: string | null) => onPrimaryChange(group.group_id, v)"
          />
          <v-switch
            :model-value="draft[group.group_id]?.fallback === 'closed'"
            label="候选都不可用时静默"
            density="compact"
            hide-details
            color="warning"
            @update:model-value="(v: boolean | null) => onFallbackChange(group.group_id, v)"
          />
          <div class="responder-preview text-caption">
            {{ respondersText(group) }}
            <v-chip v-if="group.preview?.degraded" size="x-small" color="warning" variant="tonal" class="ml-1">
              已降级
            </v-chip>
          </div>
        </div>
      </v-card-text>
    </v-card>

    <div class="text-caption mt-3" style="opacity: 0.7">
      这层只决定「同群里由哪个账号响应」；「这个群能不能触发、谁能触发」在「回复与权限」里配置。
    </div>
  </div>
</template>

<style scoped>
.responder-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  padding: 12px 0;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.08);
}

.responder-group-info {
  min-width: 200px;
  flex: 1 1 200px;
}

.responder-select {
  width: 220px;
}

.responder-preview {
  flex: 1 1 220px;
  min-width: 200px;
  opacity: 0.8;
}

@media (max-width: 600px) {
  .responder-select {
    width: 100%;
  }

  .responder-group-info,
  .responder-preview {
    min-width: 100%;
    flex-basis: 100%;
  }
}
</style>
