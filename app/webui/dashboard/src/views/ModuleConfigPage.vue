<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useModulesStore, isBuiltinModule } from '@/stores/modules'
import { useBotsStore } from '@/stores/bots'
import { useNotifyStore } from '@/stores/notify'
import { errorMessage, registerDraftFlush } from '@/api/http'
import { PERMISSION_LABELS, PERMISSION_TOOLTIP } from '@/agent/labels'
import ConfigForm from '@/components/config/ConfigForm.vue'
import SaveStatusBar from '@/components/common/SaveStatusBar.vue'
import PluginStatusBanner from '@/components/tools/PluginStatusBanner.vue'
import PluginDepsDialog from '@/components/tools/PluginDepsDialog.vue'

const route = useRoute()
const router = useRouter()
const modules = useModulesStore()
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
/** 当前草稿对应的账号/模块：用于识别「同账号同模块的纯数据刷新」 */
let draftAccountId: string | null = null
let draftModuleName = ''

/** 插件状态异常时：表单置灰，顶部横幅给「检查依赖 / 重新加载」 */
const moduleBroken = computed(() => {
  const status = mod.value?.status || 'ok'
  return status === 'error' || status === 'conflict'
})
const moduleStatusText = computed(() => (mod.value?.enabled ? '已启用' : '已停用'))
const hasPluginPage = computed(() => !!mod.value?.has_page)
const hasConfigForm = computed(() => !!(mod.value?.config_schema || Object.keys(mod.value?.config || {}).length))
const configTab = ref<'plugin' | 'basic'>('plugin')
const depsOpen = ref(false)

watch(
  () => [hasPluginPage.value, hasConfigForm.value],
  () => {
    configTab.value = hasPluginPage.value ? 'plugin' : 'basic'
  },
  { immediate: true },
)

watch(mod, (m) => {
  if (!m) return
  if (retryTimer) {
    clearTimeout(retryTimer)
    retryTimer = null
  }
  retryCount.value = 0
  // 同账号、同模块的纯数据刷新（WS 触发的重载 / 其它页面保存后重拉）不要覆盖正在编辑的草稿。
  // 切账号或换模块时 draftAccountId/draftModuleName 不匹配，照常初始化。
  const sameScope = draftModuleName === m.name && draftAccountId === modules.botId
  if (sameScope && dirtyFlag) return
  initFromModule()
})

const PERMISSION_COLOR: Record<string, string> = {
  everyone: 'grey',
  member: 'primary',
  group_admin: 'info',
  group_owner: 'purple',
  manager: 'teal',
  owner: 'error',
}

// ---------- 配置草稿 + 自动保存 ----------
const draft = reactive<Record<string, any>>({})
const saveStatus = ref<'clean' | 'dirty' | 'saving' | 'error'>('clean')
const saveMsg = ref('')
let autosaveTimer: number | null = null
let dirtyFlag = false
/** 会话过期跳转前的草稿落盘注销函数 */
let unregisterDraftFlush: (() => void) | null = null
// 编辑序号：保存期间若又有新编辑，完成时按序号判断不清理 dirty，避免最新编辑丢失
let editSeq = 0

function initFromModule() {
  const m = mod.value
  if (!m) return
  draftModuleName = m.name
  draftAccountId = modules.botId
  Object.keys(draft).forEach((k) => delete draft[k])
  Object.assign(draft, m.config || {})
  saveStatus.value = 'clean'
  saveMsg.value = ''
  dirtyFlag = false
}

function onChange(key: string, value: any) {
  draft[key] = value
  dirtyFlag = true
  editSeq += 1
  saveStatus.value = 'dirty'
  if (autosaveTimer) clearTimeout(autosaveTimer)
  autosaveTimer = window.setTimeout(doSave, 2000)
}

async function doSave(targetAccountId?: string | null) {
  // 模板 `@click="doSave"` 会把点击事件对象当第一个参数传进来：只接受字符串账号 id。
  // null 是「上一个账号不存在」的显式信号，必须保留，不能一起归一化成 undefined。
  if (targetAccountId != null && typeof targetAccountId !== 'string') targetAccountId = undefined
  if (autosaveTimer) {
    clearTimeout(autosaveTimer)
    autosaveTimer = null
  }
  if (!dirtyFlag) return
  // 显式传入 null = 上一个账号不存在（没什么可写）；传入 id = 切号前写回旧账号
  if (targetAccountId === null) return
  const snapshotSeq = editSeq
  saveStatus.value = 'saving'
  try {
    await modules.saveConfig(name, { ...draft }, targetAccountId)
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

/** 立即落盘未保存的配置（离开页面 / 切账号 / 会话过期前调用）。
 *  ``accountId`` 显式指定时写到该账号名下：切号 watcher 触发时 currentAccountId 已变成新账号。 */
async function flushDrafts(accountId?: string | null) {
  await doSave(accountId)
}

// 启停只在「功能插件」列表操作：这里只读展示状态，避免同一配置两处入口
async function reloadModule() {
  if (!mod.value) return
  try {
    await modules.reloadSingle(name)
    notify.push(`${mod.value.name} 已重新加载`, 'success')
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

async function onDepsDone() {
  try {
    await modules.load()
  } catch {
    /* 列表刷新失败不影响对话框里的结果提示 */
  }
}

// ---------- 插件自定义页 ----------
const pluginSrc = computed(() => {
  const base = `/api/module/${name}/page`
  const botId = bots.currentAccountId
  return botId !== null ? `${base}?account_id=${botId}` : base
})

// 插件自定义页的高度跟随：整个页面只有一个 iframe，观察器复用一份，重载/卸载时替换或断开，
// 以前每次 load 都 new 一个 ResizeObserver 挂在 iframe 上且从不 disconnect（反复刷新会泄漏）。
let pluginRO: ResizeObserver | null = null

function releasePluginObserver() {
  pluginRO?.disconnect()
  pluginRO = null
}

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
  releasePluginObserver()
  try {
    const doc = el.contentDocument
    if (doc?.body) {
      pluginRO = new ResizeObserver(() => resizePluginFrame(el))
      pluginRO.observe(doc.body)
    }
  } catch {
    /* 跨域忽略 */
  }
}

watch(
  () => bots.currentId,
  async (_currentId, previousId) => {
    // 先把**上一个账号**的未保存改动写到它自己名下，再拉新账号的模块数据。
    // 此时 currentId 已经是新账号，必须显式传 previousId，否则会把旧草稿写进新账号。
    await flushDrafts(previousId ?? null)
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
  await ensureModule()
  await nextTick()
})

onBeforeUnmount(() => {
  // 卸载时 2s 防抖窗口里可能还有未保存的编辑：立即落盘，而不是只 clearTimeout 丢掉
  void flushDrafts()
})

onUnmounted(() => {
  releasePluginObserver()
  unregisterDraftFlush?.()
  unregisterDraftFlush = null
  if (autosaveTimer) clearTimeout(autosaveTimer)
  if (retryTimer) clearTimeout(retryTimer)
  autosaveTimer = null
  retryTimer = null
})
</script>

<template>
  <div v-if="mod">
    <div class="app-page-header d-flex align-center flex-wrap gap-2">
      <div class="d-flex align-center gap-2 flex-wrap">
        <v-btn variant="text" icon="mdi-arrow-left" size="small" title="返回插件列表" @click="router.push('/modules')" />
        <h1 class="app-page-title">{{ mod.name }}</h1>
        <!-- 来源 / 版本：与插件卡片的编目信息一致 -->
        <v-chip size="small" variant="tonal" :color="isBuiltinModule(mod) ? 'default' : 'teal'">
          {{ isBuiltinModule(mod) ? '内置' : '第三方' }}
        </v-chip>
        <v-chip v-if="mod.version" size="small" variant="tonal">v{{ mod.version }}</v-chip>
        <v-tooltip location="top" :text="PERMISSION_TOOLTIP">
          <template #activator="{ props }">
            <v-chip v-bind="props" size="small" variant="tonal" :color="PERMISSION_COLOR[mod.permission] || 'default'">
              {{ PERMISSION_LABELS[mod.permission] || mod.permission }}
            </v-chip>
          </template>
        </v-tooltip>
        <v-chip v-if="mod.account_id" size="small" variant="tonal">{{ bots.currentBot?.name ?? mod.account_id }}</v-chip>
      </div>
      <v-spacer />
      <div class="d-flex align-center flex-wrap gap-2">
        <v-chip size="small" variant="tonal" :color="mod.enabled ? 'success' : 'grey'">
          当前状态：{{ moduleStatusText }}
        </v-chip>
        <v-btn size="small" variant="text" prepend-icon="mdi-cube-outline" @click="router.push('/modules')">
          返回插件列表
        </v-btn>
        <v-btn size="small" variant="text" @click="router.push('/modules')">
          {{ mod.enabled ? '去列表停用' : '去列表启用' }}
        </v-btn>
      </div>
    </div>
    <div class="app-page-subtitle" style="padding-left: 44px">{{ mod.description }}</div>

    <div class="d-flex align-center flex-wrap gap-2 mt-3 mb-2">
      <SaveStatusBar :status="saveStatus" autosave hint="改动会自动保存并生效" />
      <v-btn
        size="small"
        variant="text"
        prepend-icon="mdi-refresh"
        title="切换账号实例或插件异常时使用；会先保存当前改动"
        @click="reloadModule"
      >
        重新加载
      </v-btn>
    </div>

    <PluginStatusBanner
      :status="mod.status"
      :error="mod.error"
      :name="mod.name"
      @reload="reloadModule"
      @check-deps="depsOpen = true"
    />

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
        当前显示的是<strong>所有账号共用</strong>的配置，不是账号「{{ bots.currentBot?.name ?? bots.currentAccountId }}」
        的专属配置。点「重新加载」为这个账号准备好自己的配置后再编辑（当前改动会先保存）。
      </div>
    </v-alert>

    <!-- 插件异常时整块设置置灰：先按顶部横幅修好依赖/冲突 -->
    <div :class="{ 'plugin-broken': moduleBroken }">

    <!-- 插件设置：有插件自带设置页时用 Tab 与基础设置区分，避免两套界面同时出现 -->
    <v-card v-if="hasPluginPage || hasConfigForm" variant="outlined" class="mb-4">
      <v-card-title class="d-flex align-center flex-wrap gap-2">
        <v-icon icon="mdi-cogs" class="mr-2" color="primary" /> 插件设置
      </v-card-title>
      <v-tabs
        v-if="hasPluginPage && hasConfigForm"
        v-model="configTab"
        density="comfortable"
        color="primary"
      >
        <v-tab value="plugin">插件自带设置</v-tab>
        <v-tab value="basic">基础设置</v-tab>
      </v-tabs>
      <v-card-text>
        <template v-if="hasPluginPage && hasConfigForm">
          <v-window v-model="configTab">
            <v-window-item value="plugin">
              <div class="text-caption mb-2" style="opacity: 0.65">由插件自己提供，保存与生效方式由插件决定。</div>
              <iframe :src="pluginSrc" class="plugin-frame" title="插件设置页" @load="onPluginLoad" />
            </v-window-item>
            <v-window-item value="basic">
              <ConfigForm
                :module-name="name"
                :schema="mod.config_schema || {}"
                :config="draft"
                :bot-id="bots.currentAccountId"
                @change="onChange"
              />
            </v-window-item>
          </v-window>
        </template>
        <template v-else-if="hasPluginPage">
          <div class="text-caption mb-2" style="opacity: 0.65">由插件自己提供，保存与生效方式由插件决定。</div>
          <iframe :src="pluginSrc" class="plugin-frame" title="插件设置页" @load="onPluginLoad" />
        </template>
        <template v-else>
          <ConfigForm
            :module-name="name"
            :schema="mod.config_schema || {}"
            :config="draft"
            :bot-id="bots.currentAccountId"
            @change="onChange"
          />
        </template>
      </v-card-text>
    </v-card>

    <!-- 账号级「谁能回复」统一在回复与权限维护，这里只放链接，避免重复编辑 -->
    <v-card variant="outlined" class="mb-4">
      <v-card-title class="d-flex align-center">
        <v-icon icon="mdi-shield-account-outline" class="mr-2" color="primary" /> 谁能回复这个账号
      </v-card-title>
      <v-card-text>
        <div class="text-caption mb-2" style="opacity: 0.65">
          群与成员范围在「回复与权限」里统一管理，这里不再重复编辑。
        </div>
        <v-btn
          size="small"
          variant="tonal"
          prepend-icon="mdi-arrow-right"
          @click="router.push({ path: '/reply', query: { tab: 'who' } })"
        >
          去「回复与权限」
        </v-btn>
      </v-card-text>
    </v-card>
    </div>

    <PluginDepsDialog v-model="depsOpen" :module-name="name" :module-label="mod.name" @done="onDepsDone" @reload="reloadModule" />
  </div>

  <div v-else-if="modules.loading" class="empty-tip">
    <v-skeleton-loader type="heading, article, actions" class="rounded-xl w-100" style="max-width: 900px" />
  </div>
  <div v-else class="empty-tip">
    <v-icon icon="mdi-cube-off-outline" size="56" color="rgba(var(--v-theme-on-surface), 0.3)" />
    <div>插件不存在或未加载</div>
    <v-btn variant="tonal" prepend-icon="mdi-refresh" class="mt-2" :loading="modules.loading" @click="ensureModule">
      重新加载
    </v-btn>
  </div>
</template>

<style scoped>
/* 插件异常时：设置区置灰，避免用户在坏状态下继续改 */
.plugin-broken {
  opacity: 0.55;
  pointer-events: none;
}

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
</style>
