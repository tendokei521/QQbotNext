<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  useModulesStore,
  isBuiltinModule,
  type InstallPreview,
  type ModuleData,
} from '@/stores/modules'
import { displayName, useBotsStore } from '@/stores/bots'
import { useNotifyStore } from '@/stores/notify'
import { errorMessage } from '@/api/http'
import EmptyState from '@/components/EmptyState.vue'

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
const zipPreview = ref<InstallPreview | null>(null)
const zipError = ref('')
const zipValidating = ref(false)
const zipInstalling = ref(false)
const zipDragOver = ref(false)
const zipFileInput = ref<HTMLInputElement | null>(null)
/** 与内置模块同名：第三方不允许覆盖内置，确认按钮直接禁用 */
const zipBlocked = computed(() => !!zipPreview.value?.builtin_conflict)

const PERMISSION_LABEL: Record<string, string> = {
  everyone: '所有人',
  member: '群成员',
  group_admin: '管理员',
  group_owner: '群主',
  owner: '仅主人',
}

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
    notify.push(`模块 ${m.name} 暂时用不了：${statusReason(m)}`, 'warning')
  }
  router.push(`/modules/${m._key}`)
}

async function onToggle(m: ModuleData, v: boolean | null) {
  if (toggling[m._key]) return
  const enabled = !!v
  toggling[m._key] = true
  try {
    await modules.toggle(m._key, enabled)
    notify.push(`模块 ${m.name} 已${enabled ? '启用' : '禁用'}`, 'success')
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
    notify.push(`模块 ${m.name} 已重新加载`, 'success')
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

/** 内置模块：禁用并隐藏（软卸载，文件/配置保留，可在设置页恢复） */
async function onHideBuiltin(m: ModuleData) {
  if (!window.confirm(`禁用并隐藏「${m.name}」？插件文件与它的配置都会保留，之后可以恢复安装。`)) return
  try {
    await modules.uninstall(m._key)
    notify.push(`模块 ${m.name} 已禁用并隐藏`, 'success')
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

/** 第三方模块：硬删除（配置与数据默认保留） */
async function onDelete(m: ModuleData) {
  const ok = window.confirm(
    `删除插件「${m.name}」？\n\n` +
      '插件文件会被删除；它的配置与聊天数据会保留，重新安装后还能接着用。',
  )
  if (!ok) return
  try {
    await modules.deleteModule(m._key, false)
    notify.push(`插件 ${m.name} 已删除`, 'success')
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

/** 软卸载模块恢复安装（对所有账号补实例由后端完成） */
async function onRestore(name: string, displayName?: string) {
  try {
    await modules.install(name)
    notify.push(`模块 ${displayName || name} 已恢复安装`, 'success')
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
  zipValidating.value = true
  try {
    zipPreview.value = await modules.validateZip(file)
  } catch (err) {
    zipError.value = errorMessage(err)
  } finally {
    zipValidating.value = false
  }
}

async function confirmZip() {
  const file = zipFile.value
  if (!file || !zipPreview.value) return
  zipInstalling.value = true
  try {
    if (zipDialogMode.value === 'update' && zipTarget.value) {
      await modules.updateZip(zipTarget.value, file)
      notify.push(`插件 ${zipTarget.value} 已更新`, 'success')
    } else {
      await modules.installZip(file, !!zipPreview.value.conflict)
      notify.push(`插件 ${zipPreview.value.display_name || zipPreview.value.name} 安装成功`, 'success')
    }
    closeZipDialog()
  } catch (err) {
    zipError.value = errorMessage(err)
    notify.push(errorMessage(err), 'error')
  } finally {
    zipInstalling.value = false
  }
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
  if (!modules.list.length) modules.load()
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
        <div class="app-page-subtitle">共 {{ modules.count }} 个功能，开关只影响当前账号</div>
      </div>
      <v-chip v-if="bots.currentAccountId" size="small" variant="tonal" class="mr-2">
        当前账号：{{ accountName }}
      </v-chip>
      <v-btn
        variant="tonal"
        prepend-icon="mdi-package-variant"
        class="mr-2"
        @click="openInstallDialog"
      >
        安装插件
      </v-btn>
      <v-btn variant="tonal" prepend-icon="mdi-refresh" :loading="modules.reloading" @click="modules.reloadAll().catch((e: unknown) => notify.push(errorMessage(e), 'error'))">
        刷新模块
      </v-btn>
    </div>

    <!-- 顶部提示条：加载失败 / 已软卸载（可恢复） -->
    <v-alert v-if="hasIssues" type="warning" variant="tonal" density="compact" class="mb-4">
      <div class="font-weight-medium mb-1">模块状态提示</div>
      <template v-if="errorModules.length">
        <div class="text-caption mb-1">以下模块加载失败（不影响其它模块）：</div>
        <div
          v-for="m in errorModules"
          :key="`err-${m._key}`"
          class="issue-row text-caption"
        >
          <v-icon icon="mdi-alert-circle-outline" size="x-small" color="error" class="mr-1" />
          <span class="font-weight-medium">{{ m.name }}</span>
          <span class="issue-detail">{{ m.error || statusReason(m) }}</span>
          <v-btn size="x-small" variant="text" prepend-icon="mdi-refresh" @click="onReload(m)">重试加载</v-btn>
        </div>
      </template>
      <template v-if="uninstalledModules.length">
        <div class="text-caption mb-1 mt-2">以下模块已禁用并隐藏（文件与配置保留）：</div>
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

    <v-text-field
      ref="searchRef"
      v-model="search"
      placeholder="搜索功能插件…"
      density="comfortable"
      variant="outlined"
      hide-details
      clearable
      prepend-inner-icon="mdi-magnify"
      class="mb-4"
    />

    <!-- 首载骨架屏 -->
    <v-row v-if="modules.loading && modules.list.length === 0">
      <v-col v-for="i in 6" :key="i" cols="12" md="6" xl="4">
        <v-skeleton-loader type="card-avatar, article" class="rounded-xl" />
      </v-col>
    </v-row>

    <template v-else>
      <div v-for="section in sections" :key="section.key" class="mb-3">
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
          {{ section.title }}
          <v-chip size="x-small" variant="tonal" color="primary" class="ml-2">{{ section.items.length }}</v-chip>
        </div>
        <v-row v-show="!section.collapsible || !isCollapsed(section.key)">
          <v-col v-for="m in section.items" :key="m._key" cols="12" md="6" xl="4">
            <v-card variant="outlined" class="module-card app-card-hover" @click="openModule(m)">
              <v-card-item>
                <template #prepend>
                  <v-avatar color="lightprimary" size="36" rounded="lg">
                    <v-icon
                      :icon="m.pinned ? 'mdi-pin' : isBuiltinModule(m) ? 'mdi-cube-outline' : 'mdi-package-variant-closed'"
                      color="primary"
                      size="small"
                    />
                  </v-avatar>
                </template>
                <v-card-title class="text-body-1">
                  <span class="app-line-clamp-1">{{ m.name }}</span>
                  <!-- 来源徽标：内置 / 第三方；预设装的模块再标「来自预设 xxx」 -->
                  <v-chip
                    size="x-small"
                    class="ml-1"
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
                        class="ml-1 origin-chip"
                        color="primary"
                        variant="tonal"
                        prepend-icon="mdi-package-variant-closed"
                        @click="router.push('/bundles')"
                      >
                        来自「{{ m.origin.bundle_name || '未命名预设' }}」
                      </v-chip>
                    </template>
                    <div class="text-caption">
                      预设「{{ m.origin.bundle_name || '未命名预设' }}」v{{ m.origin.bundle_version }}
                    </div>
                    <div class="text-caption">
                      {{ m.origin.action === 'upgraded' ? '随预设升级' : '随预设安装' }} ·
                      {{ formatOriginTime(m.origin.installed_at) }}
                    </div>
                    <div class="text-caption">点击去配置包</div>
                  </v-tooltip>
                  <v-chip size="x-small" class="ml-1" :color="PERMISSION_COLOR[m.permission] || 'default'" variant="tonal">
                    {{ PERMISSION_LABEL[m.permission] || m.permission }}
                  </v-chip>
                  <v-tooltip v-if="m.supersedes?.length" location="top" max-width="320">
                    <template #activator="{ props }">
                      <v-chip v-bind="props" size="x-small" class="ml-1" color="warning" variant="tonal">
                        替代了{{ featureLabels(m.supersedes) }}
                      </v-chip>
                    </template>
                    <div class="text-caption">开启后，这些内置环节由本插件负责</div>
                  </v-tooltip>
                  <!-- 装坏/卸载状态：红色小标 + 悬浮错误文本 -->
                  <v-tooltip v-if="statusOf(m) === 'error'" location="top" max-width="420">
                    <template #activator="{ props }">
                      <v-chip v-bind="props" size="x-small" class="ml-1" color="error" variant="flat" prepend-icon="mdi-alert-circle">
                        加载失败
                      </v-chip>
                    </template>
                    <div class="text-caption">{{ m.error || '加载失败（无错误信息）' }}</div>
                  </v-tooltip>
                  <v-chip
                    v-else-if="statusOf(m) === 'uninstalled'"
                    size="x-small"
                    class="ml-1"
                    color="warning"
                    variant="tonal"
                  >
                    已卸载
                  </v-chip>
                  <v-tooltip v-else-if="m.manifest_missing" location="top" max-width="360">
                    <template #activator="{ props }">
                      <v-chip v-bind="props" size="x-small" class="ml-1" color="info" variant="tonal">
                        文件不完整
                      </v-chip>
                    </template>
                    <div class="text-caption">插件缺少说明文件，版本和显示名用了默认值，一般不影响使用</div>
                  </v-tooltip>
                </v-card-title>
                <v-card-subtitle class="app-line-clamp-2">
                  <span v-if="statusOf(m) === 'error'" class="text-error">{{ m.error || '加载失败' }}</span>
                  <span v-else>{{ m.description }}</span>
                </v-card-subtitle>
                <template #append>
                  <v-switch
                    :model-value="m.enabled"
                    color="primary"
                    density="compact"
                    hide-details
                    :loading="toggling[m._key]"
                    :disabled="toggling[m._key] || !isUsable(m)"
                    @click.stop
                    @update:model-value="(v: boolean | null) => onToggle(m, v)"
                  />
                  <v-menu location="bottom end">
                    <template #activator="{ props }">
                      <v-btn
                        v-bind="props"
                        icon="mdi-dots-vertical"
                        size="x-small"
                        variant="text"
                        @click.stop
                      />
                    </template>
                    <v-list density="compact">
                      <v-list-item v-if="isUsable(m)" prepend-icon="mdi-refresh" @click="onReload(m)">重载</v-list-item>
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
                          禁用并隐藏
                        </v-list-item>
                      </template>
                      <!-- 第三方：重载 / 更新… / 删除… -->
                      <template v-else>
                        <v-list-item
                          v-if="statusOf(m) === 'uninstalled'"
                          prepend-icon="mdi-restore"
                          @click="onRestore(m._key, m.name)"
                        >
                          恢复安装
                        </v-list-item>
                        <v-list-item prepend-icon="mdi-update" @click="openUpdateDialog(m)">更新…</v-list-item>
                        <v-list-item
                          prepend-icon="mdi-trash-can-outline"
                          class="text-error"
                          @click="onDelete(m)"
                        >
                          删除…
                        </v-list-item>
                      </template>
                    </v-list>
                  </v-menu>
                </template>
              </v-card-item>
            </v-card>
          </v-col>
        </v-row>
      </div>

      <v-card v-if="filtered.length === 0 && !modules.loading" variant="outlined">
        <EmptyState
          icon="mdi-magnify-close"
          :title="search ? `没有匹配「${search}」的模块` : '暂无模块'"
          description="尝试更换关键词，或刷新模块列表"
        >
          <v-btn size="small" variant="tonal" prepend-icon="mdi-refresh" @click="modules.reloadAll().catch((e: unknown) => notify.push(errorMessage(e), 'error'))">刷新模块</v-btn>
        </EmptyState>
      </v-card>
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
                  <span class="text-error">与内置模块同名（禁止覆盖）</span>
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
              {{ zipPreview.conflict_reason || '该插件与内置模块同名，第三方插件不允许覆盖内置模块。' }}
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
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" :disabled="zipInstalling" @click="closeZipDialog">取消</v-btn>
          <v-btn
            color="primary"
            variant="tonal"
            :disabled="!zipFile || !zipPreview || zipValidating || zipBlocked"
            :loading="zipInstalling"
            @click="confirmZip"
          >
            {{ zipDialogMode === 'update' ? '确认更新' : zipPreview?.conflict ? '覆盖安装' : '确认安装' }}
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<style scoped>
.group-heading {
  font-size: 14px;
  font-weight: 600;
  color: rgba(var(--v-theme-on-surface), 0.7);
  padding: 8px 2px;
  cursor: pointer;
  user-select: none;
}

.group-heading--static {
  cursor: default;
}

.module-card {
  height: 100%;
  border-radius: 14px;
  cursor: pointer;
}

.module-card :deep(.v-card-item__title) {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  min-width: 0;
  white-space: normal;
}

.module-card :deep(.v-card-item__subtitle) {
  min-height: 36px;
  line-height: 1.5;
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
</style>
