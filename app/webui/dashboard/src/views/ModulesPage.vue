<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  useModulesStore,
  isBuiltinModule,
  type InstallPreview,
  type ModuleData,
  type ZipInstallInfo,
} from '@/stores/modules'
import { displayName, useBotsStore } from '@/stores/bots'
import { useNotifyStore } from '@/stores/notify'
import { errorMessage } from '@/api/http'
import { PERMISSION_LABELS, PERMISSION_TOOLTIP } from '@/agent/labels'
import EmptyState from '@/components/EmptyState.vue'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'
import PluginDepsDialog from '@/components/tools/PluginDepsDialog.vue'

const modules = useModulesStore()
const bots = useBotsStore()
const notify = useNotifyStore()
const router = useRouter()

function formatOriginTime(ts: number): string {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false })
}

const search = ref('')
const collapsed = ref<Record<string, boolean>>({})
const toggling = reactive<Record<string, boolean>>({})
const searchRef = ref<HTMLInputElement | null>(null)

/** 功能开关是按账号存的：页头标清当前账号，避免用户以为改了全局 */
const accountName = computed(() => {
  const bot = bots.bots.find((item) => item.id === bots.currentAccountId)
  return bot ? displayName(bot) : '未选择账号'
})

// 换账号后原来的搜索词可能只匹配得上上一个账号的模块，清空避免「看起来模块没了」
watch(
  () => bots.currentAccountId,
  () => {
    search.value = ''
  },
)

// ---------- 安装 / 更新对话框 ----------
type ZipDialogMode = 'install' | 'update'
const zipDialog = ref(false)
const zipDialogMode = ref<ZipDialogMode>('install')
/** 更新模式下的目标模块名 */
const zipTarget = ref<string | null>(null)
const zipFile = ref<File | null>(null)
/**
 * 预检结果：在 store 的 InstallPreview 之上补依赖字段。
 * validate-zip 会带回这些字段，store 类型暂未包含，前端本地扩展（不改 store）。
 */
type DependencyPreview = InstallPreview & {
  deps_file?: string | null
  deps_declared?: string[]
  deps_missing?: string[]
  deps_rejected?: string[]
}
const zipPreview = ref<DependencyPreview | null>(null)
const zipError = ref('')
const zipValidating = ref(false)
const zipInstalling = ref(false)
const zipDragOver = ref(false)
const zipFileInput = ref<HTMLInputElement | null>(null)
/** 与内置模块同名：第三方不允许覆盖内置，确认按钮直接禁用 */
const zipBlocked = computed(() => !!zipPreview.value?.builtin_conflict)

/** 预检里的缺失依赖：装之前就提示，并允许确认后自动补装 */
const zipDepsMissing = computed<string[]>(() => zipPreview.value?.deps_missing || [])
const zipDepsRejected = computed<string[]>(() => zipPreview.value?.deps_rejected || [])
/** 安装时自动装依赖（需联网）：有缺失依赖时默认勾选，一键装完就能用 */
const zipAutoDeps = ref(false)
/** 可选 pip 镜像地址（留空用默认源/已配置镜像） */
const zipDepsMirror = ref('')

// ---------- 依赖安装（已安装插件走共享组件 PluginDepsDialog） ----------

/** 已安装插件的依赖检查（共享组件 PluginDepsDialog） */
const cardDepsOpen = ref(false)
const cardDepsName = ref('')
const cardDepsLabel = ref('')

const PERMISSION_COLOR: Record<string, string> = {
  everyone: 'grey',
  member: 'primary',
  group_admin: 'info',
  group_owner: 'purple',
  owner: 'error',
}

/** 编目状态；缺省（旧后端）按 ok 处理 */
function statusOf(m: ModuleData): string {
  return m.status || 'ok'
}

/** 是否可交互（开关/配置）：只有已正常加载的模块可以 */
function isUsable(m: ModuleData): boolean {
  return statusOf(m) === 'ok'
}

/** 后端 supersedes 里存的是内部能力名，界面上换成用户看得懂的说法 */
const FEATURE_LABELS: Record<string, string> = {
  agent: '对话主流程',
  assembly: '消息组装',
  provider_invoker: '模型调用',
  output: '回复输出',
}

function featureLabels(ids: string[]): string {
  return ids.map((id) => FEATURE_LABELS[id] || '内置功能').join('、')
}

/** 模块状态 → 用户能懂的原因，不把状态枚举直接甩给用户 */
const STATUS_REASON: Record<string, string> = {
  error: '加载出错，看卡片上的提示',
  conflict: '与其它插件冲突',
  uninstalled: '已禁用',
  manifest_missing: '文件不完整',
}

function statusReason(m: ModuleData): string {
  return STATUS_REASON[statusOf(m)] || '暂时不可用'
}

/** 模块 key（路由/接口里的内部名）→ 用户看到的显示名 */
function moduleDisplayName(key: string | null): string {
  if (!key) return ''
  return modules.list.find((m) => m._key === key)?.name || '该插件'
}

const filtered = computed(() => {
  const kw = search.value.trim().toLowerCase()
  if (!kw) return modules.list
  return modules.list.filter((m) => {
    const name = (m.name + ' ' + m.name_sign + ' ' + (m.tags || []).join(' ')).toLowerCase()
    return name.includes(kw)
  })
})

/** 置顶 + 分类分组（统一成 sections，避免模板重复两份卡片） */
const sections = computed(() => {
  const list = filtered.value
  const pinned = list.filter((m) => m.pinned)
  const byCategory = new Map<string, ModuleData[]>()
  list
    .filter((m) => !m.pinned)
    .forEach((m) => {
      const cat = m.category || '未分类'
      if (!byCategory.has(cat)) byCategory.set(cat, [])
      byCategory.get(cat)!.push(m)
    })
  const cats = [...byCategory.entries()].sort((a, b) => a[0].localeCompare(b[0], 'zh-CN'))
  const out: { key: string; title: string; items: ModuleData[]; collapsible: boolean }[] = []
  if (pinned.length) out.push({ key: '__pinned__', title: '置顶', items: pinned, collapsible: false })
  for (const [cat, items] of cats) {
    out.push({ key: cat, title: cat, items, collapsible: true })
  }
  return out
})

/** 加载失败（装坏）的模块：顶部提示条用 */
const errorModules = computed(() =>
  modules.list.filter((m) => ['error', 'conflict'].includes(statusOf(m))),
)
/** 软卸载（文件保留，可恢复）的模块：来自 /api/modules/uninstalled */
const uninstalledModules = computed(() => modules.uninstalledModules)
const hasIssues = computed(() => errorModules.value.length > 0 || uninstalledModules.value.length > 0)

function isCollapsed(key: string): boolean {
  // 搜索时强制展开所有分组，避免匹配结果被折叠隐藏
  if (search.value.trim()) return false
  return !!collapsed.value[key]
}

function toggleCat(key: string) {
  collapsed.value[key] = !collapsed.value[key]
  localStorage.setItem('qqbot_module_collapsed', JSON.stringify(collapsed.value))
}

function openModule(m: ModuleData) {
  if (!isUsable(m)) {
    notify.push(`插件 ${m.name} 暂时用不了：${statusReason(m)}`, 'warning')
  }
  router.push(`/modules/${m._key}`)
}

async function onToggle(m: ModuleData, v: boolean | null) {
  if (toggling[m._key]) return
  const enabled = !!v
  toggling[m._key] = true
  try {
    await modules.toggle(m._key, enabled)
    notify.push(`插件 ${m.name} 已${enabled ? '启用' : '停用'}`, 'success')
  } catch (err) {
    notify.push(errorMessage(err), 'error')
    // 通过 store.patch 改写响应式对象，失败回滚才能反映到界面
    modules.patch(m._key, { enabled: !enabled })
  } finally {
    toggling[m._key] = false
  }
}

async function onReload(m: ModuleData) {
  try {
    await modules.reloadSingle(m._key)
    notify.push(`插件 ${m.name} 已重新加载`, 'success')
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

// ---------- 危险操作确认（统一 ConfirmDialog，替代 window.confirm） ----------
interface ConfirmState {
  title: string
  message: string
  details: string[]
  danger: boolean
  confirmText: string
  run: () => Promise<void>
}

const confirmState = ref<ConfirmState | null>(null)
const confirmBusy = ref(false)

function askConfirm(state: ConfirmState) {
  confirmState.value = state
}

async function runConfirm() {
  const state = confirmState.value
  if (!state || confirmBusy.value) return
  confirmBusy.value = true
  try {
    await state.run()
    confirmState.value = null
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    confirmBusy.value = false
  }
}

/** 内置插件：停用并隐藏（文件/配置保留，之后可恢复） */
function onHideBuiltin(m: ModuleData) {
  askConfirm({
    title: `停用并隐藏「${m.name}」`,
    message: '停用后它不再出现在列表里，也不会响应消息。',
    details: ['插件文件保留', '插件配置保留', '之后可在「已禁用并隐藏」里恢复安装'],
    danger: false,
    confirmText: '停用并隐藏',
    run: async () => {
      await modules.uninstall(m._key)
      notify.push(`插件 ${m.name} 已停用并隐藏`, 'success')
    },
  })
}

/** 第三方插件：卸载（删除文件，配置与数据默认保留） */
function onDelete(m: ModuleData) {
  askConfirm({
    title: `卸载「${m.name}」`,
    message: '卸载会删除插件文件，插件配置与聊天数据默认保留。',
    details: ['删除插件文件，不能靠重新加载恢复', '插件配置与聊天数据保留', '重新安装后还能接着用'],
    danger: true,
    confirmText: '卸载',
    run: async () => {
      await modules.deleteModule(m._key, false)
      notify.push(`插件 ${m.name} 已卸载`, 'success')
    },
  })
}

/** 软卸载插件恢复安装（对所有账号补实例由后端完成） */
async function onRestore(name: string, displayName?: string) {
  try {
    await modules.install(name)
    notify.push(`插件 ${displayName || name} 已恢复安装`, 'success')
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

// ---------- 安装 / 更新对话框逻辑 ----------
function resetZipDialog() {
  zipFile.value = null
  zipPreview.value = null
  zipError.value = ''
  zipValidating.value = false
  zipInstalling.value = false
  zipDragOver.value = false
  zipAutoDeps.value = false
  zipDepsMirror.value = ''
  if (zipFileInput.value) zipFileInput.value.value = ''
}

function openInstallDialog() {
  zipDialogMode.value = 'install'
  zipTarget.value = null
  resetZipDialog()
  zipDialog.value = true
}

function openUpdateDialog(m: ModuleData) {
  if (isBuiltinModule(m)) return
  zipDialogMode.value = 'update'
  zipTarget.value = m._key
  resetZipDialog()
  zipDialog.value = true
}

function closeZipDialog() {
  if (zipInstalling.value) return
  zipDialog.value = false
  resetZipDialog()
}

function pickZipFile() {
  zipFileInput.value?.click()
}

function onPickZipFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (file) void prepareZip(file)
}

function onDropZip(event: DragEvent) {
  zipDragOver.value = false
  const file = event.dataTransfer?.files?.[0]
  if (!file) return
  if (!file.name.toLowerCase().endsWith('.zip')) {
    zipError.value = '仅支持 .zip 压缩包'
    return
  }
  void prepareZip(file)
}

/** 选文件后先 dry-run 预检：展示名称/版本/目标目录/是否覆盖，确认后再真装 */
async function prepareZip(file: File) {
  zipFile.value = file
  zipPreview.value = null
  zipError.value = ''
  zipAutoDeps.value = false
  zipDepsMirror.value = ''
  zipValidating.value = true
  try {
    zipPreview.value = (await modules.validateZip(file)) as DependencyPreview
    // 缺依赖时默认勾选自动安装：装完即用，不用再手动点一次
    zipAutoDeps.value = zipDepsMissing.value.length > 0
  } catch (err) {
    zipError.value = errorMessage(err)
  } finally {
    zipValidating.value = false
  }
}

/** 安装回执里的自动装依赖结果：失败时说清缺什么、怎么重试 */
function notifyDepsResult(info: ZipInstallInfo | null) {
  const deps = info?.deps
  if (!deps) return
  if (deps.ok) {
    const installed = deps.installed || []
    if (installed.length) notify.push(`已安装依赖：${installed.join('、')}`, 'success')
    return
  }
  const missing = (deps.missing || []).join('、')
  const tail = (deps.stderr || deps.error || '').trim().split('\n').pop() || ''
  notify.push(
    `依赖没装齐${missing ? `（${missing}）` : ''}${tail ? `：${tail}` : ''}；可在插件卡片点「检查依赖」重试`,
    'warning',
  )
}

async function confirmZip() {
  const file = zipFile.value
  if (!file || !zipPreview.value) return
  zipInstalling.value = true
  try {
    let info: ZipInstallInfo | null = null
    if (zipDialogMode.value === 'update' && zipTarget.value) {
      info = await modules.updateZip(
        zipTarget.value, file, zipAutoDeps.value, zipDepsMirror.value,
      )
      notify.push(`插件 ${zipTarget.value} 已更新`, 'success')
    } else {
      info = await modules.installZip(
        file, !!zipPreview.value.conflict, zipAutoDeps.value, zipDepsMirror.value,
      )
      notify.push(`插件 ${zipPreview.value.display_name || zipPreview.value.name} 安装成功`, 'success')
    }
    notifyDepsResult(info)
    // closeZipDialog 在安装中会直接 return：先复位标志再关，否则对话框不关、还能再点一次安装
    zipInstalling.value = false
    closeZipDialog()
  } catch (err) {
    zipError.value = errorMessage(err)
    notify.push(errorMessage(err), 'error')
  } finally {
    zipInstalling.value = false
  }
}

// ---------- 依赖安装逻辑（已安装插件走共享组件 PluginDepsDialog） ----------
function openDepsCheck(m: ModuleData) {
  cardDepsName.value = m._key
  cardDepsLabel.value = m.name
  cardDepsOpen.value = true
}

function onCardDepsDone() {
  modules.load().catch((err: unknown) => notify.push(errorMessage(err), 'error'))
}

// ---------- 键盘 / 生命周期 ----------
function onKeydown(e: KeyboardEvent) {
  const tag = (e.target as HTMLElement)?.tagName
  if (e.key === '/' && tag !== 'INPUT' && tag !== 'TEXTAREA') {
    e.preventDefault()
    searchRef.value?.focus()
  }
}

onMounted(() => {
  try {
    collapsed.value = JSON.parse(localStorage.getItem('qqbot_module_collapsed') || '{}')
  } catch {
    collapsed.value = {}
  }
  if (!modules.list.length) {
    // 首载失败要提示，不能把裸 promise 丢给全局错误处理（会误报「页面出现异常」）
    modules.load().catch((err: unknown) => notify.push(errorMessage(err), 'error'))
  }
  // 顶部"已卸载"提示条的数据源（软卸载记录）
  modules.loadUninstalled().catch(() => {
    /* 恢复区加载失败不阻塞模块列表 */
  })
  window.addEventListener('keydown', onKeydown)
})

onUnmounted(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <div>
    <div class="app-page-header">
      <div>
        <h1 class="app-page-title">功能插件</h1>
        <div class="app-page-subtitle">开关只影响当前账号</div>
      </div>
      <div class="d-flex flex-wrap gap-2">
        <v-btn color="primary" variant="flat" prepend-icon="mdi-package-variant" @click="openInstallDialog">
          安装插件
        </v-btn>
        <v-btn
          variant="tonal"
          prepend-icon="mdi-refresh"
          :loading="modules.reloading"
          @click="modules.reloadAll().catch((e: unknown) => notify.push(errorMessage(e), 'error'))"
        >
          重新加载
        </v-btn>
      </div>
    </div>

    <div class="app-toolbar">
      <v-text-field
        ref="searchRef"
        v-model="search"
        placeholder="搜索功能插件"
        density="compact"
        variant="outlined"
        hide-details
        clearable
        prepend-inner-icon="mdi-magnify"
        class="module-search"
      />
      <span v-if="bots.currentAccountId" class="app-muted">当前账号：{{ accountName }}</span>
      <v-spacer />
      <span class="app-muted">共 {{ modules.count }} 个插件</span>
    </div>

    <!-- 顶部提示条：加载失败 / 已停用并隐藏（可恢复） -->
    <v-alert v-if="hasIssues" type="warning" variant="tonal" density="compact" class="mb-4">
      <div class="font-weight-medium mb-1">插件状态提示</div>
      <template v-if="errorModules.length">
        <div class="text-caption mb-1">以下插件加载失败（不影响其它插件）：</div>
        <div
          v-for="m in errorModules"
          :key="`err-${m._key}`"
          class="issue-row text-caption"
        >
          <v-icon icon="mdi-alert-circle-outline" size="x-small" color="error" class="mr-1" />
          <span class="font-weight-medium">{{ m.name }}</span>
          <span class="issue-detail">{{ m.error || statusReason(m) }}</span>
          <v-btn
            v-if="!isBuiltinModule(m)"
            size="x-small"
            variant="text"
            prepend-icon="mdi-package-variant"
            @click="openDepsCheck(m)"
          >
            检查依赖
          </v-btn>
          <v-btn size="x-small" variant="text" prepend-icon="mdi-refresh" @click="onReload(m)">重新加载</v-btn>
        </div>
      </template>
      <template v-if="uninstalledModules.length">
        <div class="text-caption mb-1 mt-2">以下插件已停用并隐藏（文件与配置保留）：</div>
        <div
          v-for="item in uninstalledModules"
          :key="`off-${item.module_name}`"
          class="issue-row text-caption"
        >
          <v-icon icon="mdi-package-variant-remove" size="x-small" class="mr-1" />
          <span class="font-weight-medium">{{ item.display_name || item.module_name }}</span>
          <span class="issue-detail">v{{ item.version || '0.0.0' }}</span>
          <v-btn size="x-small" variant="text" prepend-icon="mdi-restore" @click="onRestore(item.module_name, item.display_name)">
            恢复安装
          </v-btn>
        </div>
      </template>
    </v-alert>

    <!-- 首载骨架屏 -->
    <div v-if="modules.loading && modules.list.length === 0" class="app-card-grid modules-grid">
      <v-skeleton-loader v-for="i in 6" :key="i" type="card-avatar, article" class="rounded-xl" />
    </div>

    <template v-else>
      <div v-for="section in sections" :key="section.key" class="plugin-section">
        <div
          class="group-heading d-flex align-center"
          :class="{ 'group-heading--static': !section.collapsible }"
          @click="section.collapsible && toggleCat(section.key)"
        >
          <v-icon
            v-if="section.collapsible"
            :icon="isCollapsed(section.key) ? 'mdi-chevron-right' : 'mdi-chevron-down'"
            size="small"
            class="mr-1"
          />
          <v-icon v-else icon="mdi-pin" size="small" class="mr-1" />
          <span class="app-section-title">{{ section.title }}</span>
          <v-chip size="x-small" variant="tonal" color="primary" class="ml-2">{{ section.items.length }}</v-chip>
        </div>

        <div v-show="!section.collapsible || !isCollapsed(section.key)" class="app-card-grid modules-grid">
          <v-card
            v-for="m in section.items"
            :key="m._key"
            variant="outlined"
            class="module-card app-card-hover"
            @click="openModule(m)"
          >
            <div class="module-card-body">
              <v-avatar color="lightprimary" size="36" rounded="lg" class="module-icon">
                <v-icon
                  :icon="m.pinned ? 'mdi-pin' : isBuiltinModule(m) ? 'mdi-cube-outline' : 'mdi-package-variant-closed'"
                  color="primary"
                  size="small"
                />
              </v-avatar>

              <div class="module-body">
                <div class="module-title-row">
                  <span class="module-name app-line-clamp-1">{{ m.name }}</span>
                  <!-- 来源徽标：内置 / 第三方；配置包装的插件再标「来自某配置包」 -->
                  <v-chip
                    size="x-small"
                    :color="isBuiltinModule(m) ? 'default' : 'teal'"
                    variant="tonal"
                  >
                    {{ isBuiltinModule(m) ? '内置' : '第三方' }}
                  </v-chip>
                  <v-tooltip v-if="m.origin" location="top" max-width="380">
                    <template #activator="{ props }">
                      <v-chip
                        v-bind="props"
                        size="x-small"
                        class="origin-chip"
                        color="primary"
                        variant="tonal"
                        prepend-icon="mdi-package-variant-closed"
                        @click.stop="router.push('/bundles')"
                      >
                        来自配置包
                      </v-chip>
                    </template>
                    <div class="text-caption">
                      配置包「{{ m.origin.bundle_name || '未命名配置包' }}」v{{ m.origin.bundle_version }}
                    </div>
                    <div class="text-caption">
                      {{ m.origin.action === 'upgraded' ? '随配置包升级' : '随配置包安装' }} ·
                      {{ formatOriginTime(m.origin.installed_at) }}
                    </div>
                    <div class="text-caption">点击去配置包</div>
                  </v-tooltip>
                  <v-tooltip location="top" :text="PERMISSION_TOOLTIP">
                    <template #activator="{ props }">
                      <v-chip v-bind="props" size="x-small" :color="PERMISSION_COLOR[m.permission] || 'default'" variant="tonal">
                        {{ PERMISSION_LABELS[m.permission] || m.permission }}
                      </v-chip>
                    </template>
                  </v-tooltip>
                  <v-tooltip v-if="m.supersedes?.length" location="top" max-width="320">
                    <template #activator="{ props }">
                      <v-chip v-bind="props" size="x-small" color="warning" variant="tonal">
                        替代了{{ featureLabels(m.supersedes) }}
                      </v-chip>
                    </template>
                    <div class="text-caption">开启后，这些内置环节由本插件负责</div>
                  </v-tooltip>
                  <!-- 装坏/卸载状态：红色小标 + 悬浮错误文本 -->
                  <v-tooltip v-if="statusOf(m) === 'error'" location="top" max-width="420">
                    <template #activator="{ props }">
                      <v-chip v-bind="props" size="x-small" color="error" variant="flat" prepend-icon="mdi-alert-circle">
                        加载失败
                      </v-chip>
                    </template>
                    <div class="text-caption">{{ m.error || '加载失败（无错误信息）' }}</div>
                  </v-tooltip>
                  <v-chip
                    v-else-if="statusOf(m) === 'uninstalled'"
                    size="x-small"
                    color="warning"
                    variant="tonal"
                  >
                    已停用
                  </v-chip>
                  <v-tooltip v-else-if="m.manifest_missing" location="top" max-width="360">
                    <template #activator="{ props }">
                      <v-chip v-bind="props" size="x-small" color="info" variant="tonal">
                        文件不完整
                      </v-chip>
                    </template>
                    <div class="text-caption">插件缺少说明文件，版本和显示名用了默认值，一般不影响使用</div>
                  </v-tooltip>
                </div>

                <div class="module-desc app-line-clamp-1">
                  <template v-if="statusOf(m) === 'error'">加载出错，可重新加载或检查依赖</template>
                  <template v-else-if="statusOf(m) === 'uninstalled'">已停用并隐藏，可在提示条恢复</template>
                  <template v-else>{{ m.description }}</template>
                </div>
              </div>

              <div class="module-actions" @click.stop>
                <v-switch
                  class="module-switch"
                  :model-value="m.enabled"
                  color="primary"
                  density="compact"
                  hide-details
                  :loading="toggling[m._key]"
                  :disabled="toggling[m._key] || !isUsable(m)"
                  @update:model-value="(v: boolean | null) => onToggle(m, v)"
                />
                <v-menu location="bottom end">
                  <template #activator="{ props }">
                    <v-btn
                      v-bind="props"
                      icon="mdi-dots-vertical"
                      size="x-small"
                      variant="text"
                    />
                  </template>
                  <v-list density="compact">
                    <v-list-item v-if="isUsable(m)" prepend-icon="mdi-refresh" @click="onReload(m)">重新加载</v-list-item>
                    <!-- 内置：禁用并隐藏（软卸载）；已卸载的内置给"恢复安装" -->
                    <template v-if="isBuiltinModule(m)">
                      <v-list-item
                        v-if="statusOf(m) === 'uninstalled'"
                        prepend-icon="mdi-restore"
                        @click="onRestore(m._key, m.name)"
                      >
                        恢复安装
                      </v-list-item>
                      <v-list-item v-else prepend-icon="mdi-eye-off-outline" @click="onHideBuiltin(m)">
                        停用（文件保留）
                      </v-list-item>
                    </template>
                    <!-- 第三方：重新加载 / 更新… / 卸载（删除文件） -->
                    <template v-else>
                      <v-list-item
                        v-if="statusOf(m) === 'uninstalled'"
                        prepend-icon="mdi-restore"
                        @click="onRestore(m._key, m.name)"
                      >
                        恢复安装
                      </v-list-item>
                      <v-list-item
                        v-if="statusOf(m) === 'error'"
                        prepend-icon="mdi-package-variant"
                        @click="openDepsCheck(m)"
                      >
                        检查依赖
                      </v-list-item>
                      <v-list-item prepend-icon="mdi-update" @click="openUpdateDialog(m)">更新…</v-list-item>
                      <v-list-item
                        prepend-icon="mdi-trash-can-outline"
                        class="text-error"
                        @click="onDelete(m)"
                      >
                        卸载（删除文件）
                      </v-list-item>
                    </template>
                  </v-list>
                </v-menu>
              </div>
            </div>
          </v-card>
        </div>
      </div>

      <EmptyState
        v-if="filtered.length === 0 && !modules.loading"
        icon="mdi-magnify-close"
        :title="search ? `没有匹配「${search}」的插件` : '暂无插件'"
        description="尝试更换关键词，或重新加载插件列表"
      >
        <v-btn size="small" variant="tonal" prepend-icon="mdi-refresh" @click="modules.reloadAll().catch((e: unknown) => notify.push(errorMessage(e), 'error'))">重新加载</v-btn>
      </EmptyState>
    </template>

    <!-- 安装 / 更新插件对话框：先预检确认，再真正安装 -->
    <v-dialog v-model="zipDialog" max-width="640" :persistent="zipInstalling" @keydown.esc="closeZipDialog">
      <v-card>
        <v-card-title class="d-flex align-center">
          <v-icon icon="mdi-package-variant" class="mr-2" color="primary" />
          {{ zipDialogMode === 'update' ? `更新插件 ${moduleDisplayName(zipTarget)}` : '安装插件' }}
          <v-spacer />
          <v-btn icon="mdi-close" variant="text" size="small" :disabled="zipInstalling" @click="closeZipDialog" />
        </v-card-title>
        <v-card-text>
          <div
            class="zip-drop"
            :class="{ 'zip-drop--over': zipDragOver }"
            @dragover.prevent="zipDragOver = true"
            @dragleave.prevent="zipDragOver = false"
            @drop.prevent="onDropZip"
          >
            <v-icon icon="mdi-cloud-upload-outline" size="36" color="primary" />
            <div class="text-body-2 mt-1">拖拽 .zip 插件包到这里</div>
            <v-btn size="small" variant="tonal" class="mt-2" @click="pickZipFile">选择文件</v-btn>
            <div v-if="zipFile" class="text-caption mt-2">{{ zipFile.name }}</div>
            <input ref="zipFileInput" type="file" accept=".zip" hidden @change="onPickZipFile" />
          </div>

          <v-progress-linear v-if="zipValidating" indeterminate color="primary" class="mt-3" />

          <v-alert v-if="zipError" type="error" variant="tonal" density="compact" class="mt-3">
            <div class="text-caption">{{ zipError }}</div>
          </v-alert>

          <!-- 预检结果：名称 / 版本 / 目标目录 / 是否覆盖 -->
          <v-card v-if="zipPreview" variant="tonal" class="mt-3">
            <v-card-text class="text-caption preview-grid">
              <div class="preview-label">插件名称</div>
              <div class="preview-value">{{ zipPreview.display_name || '未命名插件' }}</div>
              <div class="preview-label">版本</div>
              <div class="preview-value">v{{ zipPreview.version || '0.0.0' }}</div>
              <div class="preview-label">安装目标</div>
              <div class="preview-value preview-path">{{ zipPreview.target || '—' }}</div>
              <div class="preview-label">文件数</div>
              <div class="preview-value">{{ zipPreview.files ?? '—' }}</div>
              <div class="preview-label">覆盖安装</div>
              <div class="preview-value">
                <template v-if="zipPreview.builtin_conflict">
                  <span class="text-error">与内置插件同名（禁止覆盖）</span>
                </template>
                <template v-else-if="zipPreview.conflict">
                  <span class="text-warning">是</span>
                  <span v-if="zipPreview.existing_version">（已装 v{{ zipPreview.existing_version }}）</span>
                </template>
                <template v-else>否（全新安装）</template>
              </div>
            </v-card-text>
          </v-card>

          <v-alert v-if="zipPreview?.builtin_conflict" type="error" variant="tonal" density="compact" class="mt-3">
            <div class="text-caption">
              {{ zipPreview.conflict_reason || '该插件与内置插件同名，第三方插件不允许覆盖内置插件。' }}
              请让插件作者改名后重新打包。
            </div>
          </v-alert>
          <v-alert v-else-if="zipPreview?.conflict" type="warning" variant="tonal" density="compact" class="mt-3">
            <div class="text-caption">
              已存在同名第三方插件，确认后将<strong>覆盖安装</strong>：旧版本会自动备份，原插件的配置与聊天数据保留。
            </div>
          </v-alert>
          <v-alert
            v-else-if="zipDialogMode === 'update' && zipPreview && zipTarget && zipPreview.name !== zipTarget"
            type="warning"
            variant="tonal"
            density="compact"
            class="mt-3"
          >
            <div class="text-caption">
              包内插件是「{{ zipPreview.display_name || '未命名插件' }}」，与要更新的「{{ moduleDisplayName(zipTarget) }}」不一致；确认后将按包内插件安装。
            </div>
          </v-alert>

          <!-- 依赖预检：装之前说清缺什么；勾选后随安装一起用 pip 补装（需联网） -->
          <v-alert
            v-if="zipPreview && zipDepsRejected.length"
            type="warning"
            variant="tonal"
            density="compact"
            class="mt-3"
          >
            <div class="text-caption">
              这个插件声明了 {{ zipDepsRejected.length }} 项无法自动安装的写法，请让作者改成「包名 + 版本」。
            </div>
          </v-alert>
          <v-alert
            v-else-if="zipPreview && zipDepsMissing.length"
            type="warning"
            variant="tonal"
            density="compact"
            class="mt-3"
          >
            <div class="text-caption">这个插件还需要安装 {{ zipDepsMissing.length }} 个运行组件才能用：</div>
            <div class="d-flex flex-wrap gap-1 mt-1">
              <v-chip v-for="dep in zipDepsMissing" :key="dep" size="x-small" variant="tonal">{{ dep }}</v-chip>
            </div>
            <v-checkbox
              v-model="zipAutoDeps"
              density="compact"
              hide-details
              class="mt-1"
              label="安装时自动安装这些组件（需要联网）"
            />
            <v-text-field
              v-if="zipAutoDeps"
              v-model="zipDepsMirror"
              label="pip 镜像地址（可选）"
              placeholder="https://pypi.tuna.tsinghua.edu.cn/simple"
              density="compact"
              variant="outlined"
              hide-details
              class="mt-1"
            />
          </v-alert>

          <div class="text-caption mt-3" style="opacity: 0.65">
            要导入的是整账号配置（配置包 .zip）？
            <v-btn size="x-small" variant="text" @click="router.push('/bundles')">去配置包</v-btn>
          </div>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" :disabled="zipInstalling" @click="closeZipDialog">取消</v-btn>
          <v-btn
            color="primary"
            variant="tonal"
            :disabled="!zipFile || !zipPreview || zipValidating || zipBlocked || zipInstalling"
            :loading="zipInstalling"
            @click="confirmZip"
          >
            {{ zipDialogMode === 'update' ? '确认更新' : zipPreview?.conflict ? '覆盖安装' : '确认安装' }}
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 已安装插件：检查/安装运行组件（共享组件） -->
    <PluginDepsDialog
      v-model="cardDepsOpen"
      :module-name="cardDepsName"
      :module-label="cardDepsLabel"
      @done="onCardDepsDone"
      @reload="onCardDepsDone"
    />

    <!-- 危险操作统一确认：删除/停用都先讲清后果 -->
    <ConfirmDialog
      :model-value="confirmState !== null"
      :title="confirmState?.title || ''"
      :message="confirmState?.message || ''"
      :details="confirmState?.details || []"
      :danger="!!confirmState?.danger"
      :confirm-text="confirmState?.confirmText || '确定'"
      :loading="confirmBusy"
      @update:model-value="(v: boolean) => { if (!v) confirmState = null }"
      @confirm="runConfirm"
    />
  </div>
</template>

<style scoped>
.group-heading {
  padding: 4px 2px 8px;
  cursor: pointer;
  user-select: none;
}

.group-heading--static {
  cursor: default;
}

.plugin-section {
  margin-bottom: 18px;
}

.module-card {
  height: 100%;
  border-radius: 12px;
  cursor: pointer;
}

.module-card-body {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 14px 14px 16px;
}

.module-icon {
  flex: 0 0 auto;
}

.module-body {
  flex: 1 1 auto;
  min-width: 0;
}

.module-title-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px 6px;
  min-width: 0;
}

.module-name {
  font-size: 15px;
  font-weight: 600;
  line-height: 1.35;
  max-width: 100%;
}

.module-desc {
  margin-top: 3px;
  font-size: 12.5px;
  line-height: 1.5;
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.module-actions {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 2px;
}

.module-switch {
  margin: 0;
}

.module-search {
  max-width: 320px;
  flex: 1 1 220px;
}

.issue-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  padding: 2px 0;
}

.issue-detail {
  color: rgba(var(--v-theme-on-surface), 0.65);
  overflow-wrap: anywhere;
}

.zip-drop {
  border: 1px dashed rgba(var(--v-theme-on-surface), 0.25);
  border-radius: 12px;
  padding: 20px 16px;
  display: flex;
  flex-direction: column;
  align-items: center;
  transition: border-color 0.15s ease, background-color 0.15s ease;
}

.zip-drop--over {
  border-color: rgb(var(--v-theme-primary));
  background-color: rgba(var(--v-theme-primary), 0.06);
}

.preview-grid {
  display: grid;
  grid-template-columns: 84px 1fr;
  gap: 6px 12px;
}

.preview-label {
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.preview-value {
  overflow-wrap: anywhere;
}

.preview-path {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
}

.origin-chip {
  cursor: pointer;
}

/* 插件卡片固定每排三个（宽屏不再自动变成 4 个）；平板两列、手机单列。
   用组合选择器压过全局 .app-card-grid 的 auto-fill。 */
.app-card-grid.modules-grid {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

@media (max-width: 1200px) {
  .app-card-grid.modules-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 720px) {
  .app-card-grid.modules-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}

/* 窄屏：卡片单列，开关与文字间距收紧，避免操作区被挤出可视范围 */
@media (max-width: 600px) {
  .module-card-body {
    padding: 12px 12px 12px 14px;
    gap: 10px;
  }

  .issue-row {
    align-items: flex-start;
  }

  .module-search {
    flex: 1 1 100%;
    max-width: none;
  }
}

.deps-command {
  background: rgba(var(--v-theme-on-surface), 0.04);
  border-radius: 8px;
  padding: 8px 10px;
  font-size: 12px;
  max-height: 180px;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
