<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useModulesStore, isBuiltinModule, type PermissionConfig } from '@/stores/modules'
import { useResponderStore, type ResponderGroup } from '@/stores/responder'
import { useBotsStore, displayName } from '@/stores/bots'
import { useNotifyStore } from '@/stores/notify'
import { errorMessage, registerDraftFlush } from '@/api/http'
import ConfigForm from '@/components/config/ConfigForm.vue'
import PermissionEditor from '@/components/config/PermissionEditor.vue'

const route = useRoute()
const router = useRouter()
const modules = useModulesStore()
const responder = useResponderStore()
const bots = useBotsStore()
const notify = useNotifyStore()

const name = String(route.params.name)
const mod = computed(() => {
  const m = modules.modules[name]
  if (m) return m
  // 容错：兼容以显示名 / 简称访问的 URL（旧书签等）
  return modules.list.find((x) => x.name === name || x.name_sign === name)
})

// 加载兜底：模块数据未就绪时自动重试，避免启动期/网络抖动导致永久"不存在"
const retryCount = ref(0)
let retryTimer: number | null = null

watch(mod, (m) => {
  if (m) {
    initFromModule()
    if (retryTimer) {
      clearTimeout(retryTimer)
      retryTimer = null
    }
    retryCount.value = 0
  }
})

const PERMISSION_LABEL: Record<string, string> = {
  everyone: '不限制',
  member: '成员',
  group_admin: '管理员',
  group_owner: '群主',
  owner: '所有者',
}

const PERMISSION_COLOR: Record<string, string> = {
  everyone: 'grey',
  member: 'primary',
  group_admin: 'info',
  group_owner: 'purple',
  owner: 'error',
}

// ---------- 配置草稿 + 自动保存 ----------
const draft = reactive<Record<string, any>>({})
const permission = ref<PermissionConfig>({
  group_mode: 'blacklist',
  group_list: [],
  user_mode: 'blacklist',
  user_list: [],
})
const saveStatus = ref<'clean' | 'dirty' | 'saving' | 'error'>('clean')
const saveMsg = ref('')
let autosaveTimer: number | null = null
let permTimer: number | null = null
let dirtyFlag = false
let permDirtyFlag = false
/** 会话过期跳转前的草稿落盘注销函数 */
let unregisterDraftFlush: (() => void) | null = null
// 编辑序号：保存期间若又有新编辑，完成时按序号判断不清理 dirty，避免最新编辑丢失
let editSeq = 0
let permEditSeq = 0

function initFromModule() {
  const m = mod.value
  if (!m) return
  Object.keys(draft).forEach((k) => delete draft[k])
  Object.assign(draft, m.config || {})
  permission.value = {
    group_mode: m.permission_config?.group_mode || 'blacklist',
    group_list: [...(m.permission_config?.group_list || [])],
    user_mode: m.permission_config?.user_mode || 'blacklist',
    user_list: [...(m.permission_config?.user_list || [])],
  }
  saveStatus.value = 'clean'
  saveMsg.value = ''
  dirtyFlag = false
  permDirtyFlag = false
}

function onChange(key: string, value: any) {
  draft[key] = value
  dirtyFlag = true
  editSeq += 1
  saveStatus.value = 'dirty'
  if (autosaveTimer) clearTimeout(autosaveTimer)
  autosaveTimer = window.setTimeout(doSave, 2000)
}

async function doSave() {
  if (autosaveTimer) {
    clearTimeout(autosaveTimer)
    autosaveTimer = null
  }
  if (!dirtyFlag) return
  const snapshotSeq = editSeq
  saveStatus.value = 'saving'
  try {
    await modules.saveConfig(name, { ...draft })
    if (editSeq !== snapshotSeq) return // 保存期间有新编辑：保留 dirty，交由下一次保存
    dirtyFlag = false
    saveStatus.value = 'clean'
    saveMsg.value = `已保存 ${new Date().toLocaleTimeString('zh-CN', { hour12: false })}`
  } catch (err) {
    if (editSeq !== snapshotSeq) return
    saveStatus.value = 'error'
    notify.push(errorMessage(err), 'error')
  }
}

function onPermissionChange(v: PermissionConfig) {
  permission.value = v
  permDirtyFlag = true
  permEditSeq += 1
  if (permTimer) clearTimeout(permTimer)
  permTimer = window.setTimeout(doSavePermission, 2000)
}

async function doSavePermission() {
  if (permTimer) {
    clearTimeout(permTimer)
    permTimer = null
  }
  if (!permDirtyFlag) return
  const snapshotSeq = permEditSeq
  try {
    await modules.savePermission(name, permission.value)
    if (permEditSeq !== snapshotSeq) return // 保存期间有新编辑：保留 dirty
    permDirtyFlag = false
    notify.push('权限已保存', 'success')
  } catch (err) {
    if (permEditSeq !== snapshotSeq) return
    notify.push(errorMessage(err), 'error')
  }
}

/** 立即落盘配置与权限的未保存改动（离开页面 / 切账号 / 会话过期前调用）。 */
async function flushDrafts() {
  await doSave()
  await doSavePermission()
}

// ---------- 启用开关 ----------
async function onToggleEnabled(v: boolean | null) {
  if (!mod.value) return
  try {
    await modules.toggle(name, !!v)
    notify.push(`模块 ${mod.value.name} 已${v ? '启用' : '禁用'}`, 'success')
  } catch (err) {
    notify.push(errorMessage(err), 'error')
    // 走 store.patch 改写响应式对象，避免改到 list 派生出的非响应副本
    modules.patch(name, { enabled: !v })
  }
}

// ---------- 响应权策略（同群多账号） ----------
// 声明制能力：只有后端回报 single_service=true 的模块才显示策略卡片。
const RESPONDER_POLICY_ITEMS = [
  { title: '全部响应（默认）', value: 'all' },
  { title: '去重：只一个响应', value: 'dedupe' },
  { title: '固定账号响应', value: 'delegate' },
  { title: '主备接管', value: 'leader' },
]
const responderData = computed(() => responder.data[name] ?? null)
const responderDeclared = computed(() => !!mod.value?.single_service)
const responderDraft = ref<Record<string, { policy: string; primary_account_id: string | null; fallback: string }>>({})
const responderDirty = ref(false)
const responderGroups = computed(() => (responderData.value?.groups || []).filter((g) => g.accounts.length > 0))
const responderLoading = computed(() => !!responder.loading[name])
const responderSaving = computed(() => !!responder.saving[name])

function syncResponderDraft() {
  const data = responderData.value
  if (!data) return
  const next: Record<string, { policy: string; primary_account_id: string | null; fallback: string }> = {}
  data.groups.forEach((group) => {
    next[group.group_id] = {
      policy: group.policy || 'all',
      primary_account_id: group.primary_account_id,
      fallback: group.fallback || 'open',
    }
  })
  responderDraft.value = next
  responderDirty.value = false
}

async function loadResponder() {
  if (!responderDeclared.value) return
  try {
    await responder.load(name)
    syncResponderDraft()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

watch(responderData, syncResponderDraft)

function accountName(accountId: string): string {
  const bot = bots.bots.find((item) => item.id === accountId)
  return bot ? displayName(bot) : accountId
}

function needsPrimary(policy: string | undefined): boolean {
  return policy === 'delegate' || policy === 'leader'
}

function primaryItems(group: ResponderGroup) {
  return [
    { title: '未指定（按 fallback）', value: null },
    ...group.accounts.map((accountId) => ({ title: accountName(accountId), value: accountId })),
  ]
}

function onPolicyChange(groupId: string, policy: string) {
  const draft = responderDraft.value[groupId]
  if (!draft) return
  draft.policy = policy
  if (needsPrimary(policy) && !draft.primary_account_id) {
    draft.primary_account_id = responderGroups.value.find((g) => g.group_id === groupId)?.accounts[0] ?? null
  }
  responderDirty.value = true
}

function onPrimaryChange(groupId: string, primary: string | null) {
  const draft = responderDraft.value[groupId]
  if (!draft) return
  draft.primary_account_id = primary || null
  responderDirty.value = true
}

function onFallbackChange(groupId: string, silent: boolean | null) {
  const draft = responderDraft.value[groupId]
  if (!draft) return
  draft.fallback = silent ? 'closed' : 'open'
  responderDirty.value = true
}

function respondersText(group: ResponderGroup): string {
  const preview = group.preview
  if (preview?.responders?.length) {
    return `当前响应：${preview.responders.map(accountName).join('、')}（${preview.reason}）`
  }
  if (preview?.degraded) return `当前无人响应（${preview.reason}）`
  return '当前无人响应'
}

async function onSaveResponder() {
  const policies = Object.entries(responderDraft.value).map(([groupId, draft]) => ({
    group_id: groupId,
    policy: draft.policy,
    primary_account_id: draft.primary_account_id,
    fallback: draft.fallback,
  }))
  try {
    const result = await responder.save(name, policies)
    responderDirty.value = false
    if (result.warnings.length) notify.push(result.warnings.join('；'), 'warning')
    else notify.push(result.message, 'success')
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

// ---------- 插件自定义页 ----------
const pluginSrc = computed(() => {
  const base = `/api/module/${name}/page`
  const botId = bots.currentAccountId
  return botId !== null ? `${base}?account_id=${botId}` : base
})

function resizePluginFrame(el: HTMLIFrameElement | null) {
  if (!el || !el.contentDocument) return
  try {
    const h = Math.max(
      el.contentDocument.documentElement.scrollHeight,
      el.contentDocument.body?.scrollHeight || 0,
    )
    if (h > 40) el.style.height = `${h}px`
  } catch {
    /* 跨域忽略 */
  }
}

function onPluginLoad(e: Event) {
  const el = e.target as HTMLIFrameElement
  resizePluginFrame(el)
  try {
    const doc = el.contentDocument
    if (doc?.body) {
      const ro = new ResizeObserver(() => resizePluginFrame(el))
      ro.observe(doc.body)
      ;(el as any).__pluginRO = ro
    }
  } catch {
    /* 跨域忽略 */
  }
}

watch(
  () => bots.currentId,
  async () => {
    // 先把当前账号的未保存改动写完，再拉新账号的模块数据（否则草稿会被 initFromModule 覆盖）
    await flushDrafts()
    loadResponder()
    // 切换账号后重拉模块配置
    modules.load()
  },
)

/** 模块数据未就绪时的自动重试（最多 3 次，间隔递增） */
async function ensureModule() {
  if (mod.value || retryCount.value >= 3) return
  retryCount.value++
  try {
    await modules.load()
  } catch {
    /* 下次重试继续 */
  }
  if (!mod.value && retryCount.value < 3) {
    retryTimer = window.setTimeout(ensureModule, 800 * retryCount.value)
  }
}

onMounted(async () => {
  unregisterDraftFlush = registerDraftFlush(flushDrafts)
  if (!mod.value) {
    await modules.load()
  }
  initFromModule()
  loadResponder()
  await ensureModule()
  await nextTick()
})

onBeforeUnmount(() => {
  // 卸载时 2s 防抖窗口里可能还有未保存的编辑：立即落盘，而不是只 clearTimeout 丢掉
  void flushDrafts()
})

onUnmounted(() => {
  unregisterDraftFlush?.()
  unregisterDraftFlush = null
  if (autosaveTimer) clearTimeout(autosaveTimer)
  if (permTimer) clearTimeout(permTimer)
  if (retryTimer) clearTimeout(retryTimer)
  autosaveTimer = null
  permTimer = null
  retryTimer = null
})
</script>

<template>
  <div v-if="mod">
    <div class="app-page-header" style="align-items: center">
      <div class="d-flex align-center gap-2 flex-wrap">
        <v-btn variant="text" icon="mdi-arrow-left" size="small" title="返回模块列表" @click="router.push('/modules')" />
        <h1 class="app-page-title">{{ mod.name }}</h1>
        <v-chip size="small" variant="tonal" color="primary">{{ mod.name_sign }}</v-chip>
        <!-- 来源 / 版本：与模块卡片的编目信息一致 -->
        <v-chip size="small" variant="tonal" :color="isBuiltinModule(mod) ? 'default' : 'teal'">
          {{ isBuiltinModule(mod) ? '内置' : '第三方' }}
        </v-chip>
        <v-chip v-if="mod.version" size="small" variant="tonal">v{{ mod.version }}</v-chip>
        <v-chip size="small" variant="tonal" :color="PERMISSION_COLOR[mod.permission] || 'default'">
          {{ PERMISSION_LABEL[mod.permission] || mod.permission }}
        </v-chip>
        <v-chip v-if="mod.account_id" size="small" variant="tonal">{{ bots.currentBot?.name ?? mod.account_id }}</v-chip>
        <v-chip v-if="saveStatus === 'saving'" size="small" color="primary" variant="flat" class="ml-2">
          <v-progress-circular size="12" indeterminate class="mr-1" /> 保存中…
        </v-chip>
        <v-chip v-else-if="saveStatus === 'error'" size="small" color="error" variant="flat" class="ml-2">保存失败</v-chip>
        <v-chip v-else-if="saveStatus === 'dirty'" size="small" color="warning" variant="flat" class="ml-2">未保存</v-chip>
        <v-chip v-else-if="saveMsg" size="small" variant="flat" class="ml-2">{{ saveMsg }}</v-chip>
      </div>
      <div class="d-flex align-center gap-3">
        <v-switch :model-value="mod.enabled" color="primary" label="启用" density="compact" hide-details @update:model-value="onToggleEnabled" />
      </div>
    </div>
    <div class="app-page-subtitle" style="padding-left: 44px">{{ mod.description }}</div>

    <!--
      该账号的模块实例尚未装配时，后端会回退到全局实例（account_id 为 null）。
      这种条目的配置不是本账号的，改了也不算数——必须显式提示，别再让人静默写错抽屉。
    -->
    <v-alert
      v-if="!mod.account_id && bots.currentAccountId"
      type="warning"
      variant="tonal"
      density="compact"
      class="mt-3 mb-3"
    >
      <div class="text-caption">
        当前显示的是<strong>全局实例</strong>的配置，不是账号「{{ bots.currentBot?.name ?? bots.currentAccountId }}」
        的配置。点「重新加载」装配该账号的模块实例后再编辑。
      </div>
    </v-alert>

    <!-- 响应权策略（同群多账号；仅声明支持该能力的模块显示） -->
    <v-card v-if="responderDeclared" variant="outlined" class="mb-4">
      <v-card-title class="d-flex align-center flex-wrap gap-2">
        <v-icon icon="mdi-account-multiple-check" class="mr-2" color="primary" />
        响应权策略
        <v-chip size="x-small" variant="tonal">同群多账号 · 对所有账号生效</v-chip>
        <v-spacer />
        <v-btn
          size="small"
          color="primary"
          variant="tonal"
          prepend-icon="mdi-content-save"
          :loading="responderSaving"
          :disabled="!responderDirty"
          @click="onSaveResponder"
        >
          保存策略
        </v-btn>
      </v-card-title>
      <v-card-text>
        <div class="text-caption mb-3">
          默认每个账号各自响应；需要「同一个群只让一个账号处理这个功能」时再按群配置。
        </div>
        <div v-if="responderLoading" class="text-caption">加载中…</div>
        <div v-else-if="!responderGroups.length" class="text-caption">
          暂无可配置的群：需要至少一个账号在线并已拉取到群列表。
        </div>
        <div v-for="group in responderGroups" :key="group.group_id" class="responder-row">
          <div class="responder-group-info">
            <div class="font-weight-medium">{{ group.name }}</div>
            <div class="text-caption">{{ group.group_id }} · {{ group.accounts.map(accountName).join('、') }}</div>
          </div>
          <v-select
            :model-value="responderDraft[group.group_id]?.policy || 'all'"
            :items="RESPONDER_POLICY_ITEMS"
            density="compact"
            variant="outlined"
            hide-details
            style="max-width: 170px"
            @update:model-value="(v: string) => onPolicyChange(group.group_id, v)"
          />
          <v-select
            :model-value="responderDraft[group.group_id]?.primary_account_id ?? null"
            :items="primaryItems(group)"
            :disabled="!needsPrimary(responderDraft[group.group_id]?.policy)"
            density="compact"
            variant="outlined"
            hide-details
            style="max-width: 170px"
            @update:model-value="(v: string | null) => onPrimaryChange(group.group_id, v)"
          />
          <v-switch
            :model-value="responderDraft[group.group_id]?.fallback === 'closed'"
            label="无候选时静默"
            density="compact"
            hide-details
            color="warning"
            @update:model-value="(v: boolean | null) => onFallbackChange(group.group_id, v)"
          />
          <div class="responder-preview text-caption">{{ respondersText(group) }}</div>
          <v-chip v-if="group.preview?.degraded" size="x-small" color="warning" variant="tonal">已降级</v-chip>
        </div>
      </v-card-text>
    </v-card>

    <!-- 插件自定义配置页 -->
    <v-card v-if="mod.has_page" variant="outlined" class="mb-4">
      <v-card-text class="pa-2">
        <iframe :src="pluginSrc" class="plugin-frame" title="模块配置页" @load="onPluginLoad" />
      </v-card-text>
    </v-card>

    <!-- 权限配置 -->
    <v-card variant="outlined" class="mb-4">
      <v-card-title class="d-flex align-center">
        <v-icon icon="mdi-shield-account-outline" class="mr-2" color="primary" /> 响应范围控制
        <v-spacer />
        <v-btn size="small" variant="tonal" prepend-icon="mdi-content-save" @click="doSavePermission">保存权限</v-btn>
      </v-card-title>
      <v-card-text>
        <PermissionEditor :model-value="permission" @update:model-value="onPermissionChange" />
      </v-card-text>
    </v-card>

    <!-- 业务配置 -->
    <v-card v-if="mod.config_schema || Object.keys(mod.config || {}).length" variant="outlined" class="mb-4">
      <v-card-title class="d-flex align-center">
        <v-icon icon="mdi-cogs" class="mr-2" color="primary" /> 业务配置
        <v-spacer />
        <v-btn size="small" color="primary" variant="tonal" prepend-icon="mdi-content-save" :loading="saveStatus === 'saving'" @click="doSave">
          保存配置
        </v-btn>
      </v-card-title>
      <v-card-text>
        <ConfigForm
          :module-name="name"
          :schema="mod.config_schema || {}"
          :config="draft"
          :bot-id="bots.currentAccountId"
          @change="onChange"
        />
      </v-card-text>
    </v-card>
  </div>

  <div v-else-if="modules.loading" class="empty-tip">
    <v-skeleton-loader type="heading, article, actions" class="rounded-xl w-100" style="max-width: 900px" />
  </div>
  <div v-else class="empty-tip">
    <v-icon icon="mdi-cube-off-outline" size="56" color="rgba(var(--v-theme-on-surface), 0.3)" />
    <div>模块不存在或未加载</div>
    <v-btn variant="tonal" prepend-icon="mdi-refresh" class="mt-2" :loading="modules.loading" @click="ensureModule">
      重新加载
    </v-btn>
  </div>
</template>

<style scoped>
.plugin-frame {
  width: 100%;
  min-height: 240px;
  border: none;
  border-radius: 10px;
}

.empty-tip {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 60px 0;
  color: rgba(var(--v-theme-on-surface), 0.5);
}

.responder-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  padding: 10px 0;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.08);
}

.responder-group-info {
  min-width: 200px;
  flex: 1 1 200px;
}

.responder-preview {
  flex: 1 1 220px;
  color: rgba(var(--v-theme-on-surface), 0.65);
}
</style>
