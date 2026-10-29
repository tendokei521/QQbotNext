<script setup lang="ts">
/**
 * 配置包：导出一个账号的可迁移配置（人格卡 / 模型池 / Agent 配置 / 模块参数与启停 /
 * 第三方模块代码），并把它应用到目标账号（事务写 + 快照，失败自动回滚）。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { displayName, useBotsStore } from '@/stores/bots'
import { useBundlesStore, type BundleHealth, type ExportModuleOption, type ModuleDepsLog, type ReusePreset } from '@/stores/bundles'
import { useNotifyStore } from '@/stores/notify'
import { errorMessage } from '@/api/http'
import EmptyState from '@/components/EmptyState.vue'
import BundleImportDialog from '@/components/bundles/BundleImportDialog.vue'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'
import PluginDepsDialog from '@/components/tools/PluginDepsDialog.vue'

const router = useRouter()
const bots = useBotsStore()
const bundles = useBundlesStore()
const notify = useNotifyStore()

const importDialog = ref(false)
const exportDialog = ref(false)
const exporting = ref(false)
const exportOptions = ref<ExportModuleOption[]>([])
const exportSelected = reactive<Record<string, boolean>>({})
const reusePresets = ref<ReusePreset[]>([])
const reusePresetId = ref<string | null>(null)
const exportForm = reactive({
  id: '',
  name: '',
  version: '1.0.0',
  description: '',
})

const reusePresetOptions = computed(() =>
  reusePresets.value.map((preset) => ({
    id: preset.id,
    label: `${preset.name}${preset.config?.api_base ? ` · ${preset.config.api_base}` : ''}`,
  })),
)

// ---- 应用到账号（P3） ----
const applyDialog = ref(false)
const applyTarget = ref<{ id: string; name: string; version: string; secrets?: { key: string; label: string; required: boolean }[] } | null>(null)
const applySecrets = reactive<Record<string, string>>({})
/** 应用后自动安装随包模块依赖（需联网；默认不勾选，列表里看不到缺包清单） */
const applyInstallDeps = ref(false)
const applyDepsMirror = ref('')
const applyResult = ref<{
  activation_id: string
  plan: { creates: any[]; updates: any[]; reused_provider_preset_id?: string }
  modules_installed?: string[]
  modules_upgraded?: string[]
  modules_deps?: ModuleDepsLog[]
  health?: BundleHealth
} | null>(null)

// ---- 卸载（P7-6：可保留随包模块） ----
const uninstallDialog = ref(false)
const uninstallTarget = ref<{ id: string; name: string } | null>(null)
const keepModules = ref(false)
const uninstalling = ref(false)

function statusText(status: string): string {
  return {
    active: '已应用',
    failed: '失败（已回滚）',
    rolled_back: '已回滚',
    applying: '应用中…',
  }[status] || status
}

function statusColor(status: string): string {
  return status === 'active' ? 'success' : status === 'failed' ? 'error' : status === 'rolled_back' ? 'grey' : 'warning'
}

/** 依赖安装结果压成一行：已装了什么 / 还缺什么 */
function depsLine(item: ModuleDepsLog): string {
  const deps = item.deps || {}
  if (deps.ok) {
    const installed = deps.installed || []
    return installed.length ? `已安装 ${installed.join('、')}` : '依赖已就绪'
  }
  const missing = (deps.missing || []).join('、')
  const tail = (deps.stderr || deps.error || '').trim().split('\n').pop() || ''
  return `未装齐${missing ? `：${missing}` : ''}${tail ? `（${tail}）` : ''}`
}

/** 应用记录里存的是配置包 id，界面上换成配置包名（已卸载时给一句人话） */
function bundleName(bundleId: string): string {
  return bundles.list.find((item) => item.id === bundleId)?.name || '已卸载的配置包'
}

function openApply(item: { id: string; name: string; version: string; secrets?: { key: string; label: string; required: boolean }[] }) {
  if (!exportEnabled.value) {
    notify.push('先在顶部选择一个账号', 'warning')
    return
  }
  applyTarget.value = item
  applyResult.value = null
  reusePresetId.value = null
  applyInstallDeps.value = false
  applyDepsMirror.value = ''
  Object.keys(applySecrets).forEach((key) => delete applySecrets[key])
  for (const secret of item.secrets || []) applySecrets[secret.key] = ''
  applyDialog.value = true
  void loadReusePresets()
}

async function loadReusePresets() {
  try {
    reusePresets.value = await bundles.loadReusePresets()
  } catch (err) {
    reusePresets.value = []
  }
}

async function doApply() {
  if (!applyTarget.value || !bots.currentAccountId) return
  // 必填校验交给后端：目标账号可能已填过同名密钥（升级场景会复用），前端不该先拦。
  try {
    applyResult.value = await bundles.applyBundle(
      applyTarget.value.id,
      bots.currentAccountId,
      { ...applySecrets },
      reusePresetId.value || '',
      applyInstallDeps.value,
      applyDepsMirror.value,
    )
    notify.push('已应用到当前账号', 'success')
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

async function onUninstall(bundleId: string) {
  const target = bundles.list.find((item) => item.id === bundleId)
  uninstallTarget.value = { id: bundleId, name: target?.name || bundleId }
  keepModules.value = false
  uninstallDialog.value = true
}

async function doUninstall() {
  if (!uninstallTarget.value) return
  uninstalling.value = true
  try {
    await bundles.uninstallBundle(uninstallTarget.value.id, keepModules.value)
    notify.push(
      keepModules.value ? '配置包已卸载（插件保留）' : '配置包已卸载',
      'success',
    )
    uninstallDialog.value = false
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    uninstalling.value = false
  }
}

// ---- 回滚：统一 ConfirmDialog，替代 window.confirm ----
const rollbackTarget = ref('')
const rollbackOpen = ref(false)
const rollbackBusy = ref(false)

function onRollback(activationId: string) {
  rollbackTarget.value = activationId
  rollbackOpen.value = true
}

async function doRollback() {
  rollbackBusy.value = true
  try {
    await bundles.rollbackActivation(rollbackTarget.value)
    notify.push('已回滚', 'success')
    rollbackOpen.value = false
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    rollbackBusy.value = false
  }
}

// ---- 缺少运行组件：直接复用插件的依赖安装能力 ----
const depsOpen = ref(false)
const depsModuleName = ref('')
const depsModuleLabel = ref('')

function openDeps(moduleName: string, label?: string) {
  depsModuleName.value = moduleName
  depsModuleLabel.value = label || moduleName
  depsOpen.value = true
}

async function onDepsDone() {
  try {
    await bundles.load()
  } catch {
    /* 依赖安装结果已由对话框提示；列表刷新失败不阻塞 */
  }
}

const accountName = computed(() => {
  const bot = bots.bots.find((item) => item.id === bots.currentAccountId)
  return bot ? displayName(bot) : (bots.currentAccountId || '未选择账号')
})

const exportEnabled = computed(() => Boolean(bots.currentAccountId))

function formatTime(ts: number): string {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false })
}

function formatSize(bytes: number): string {
  if (!bytes) return '—'
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

async function onImport() {
  importDialog.value = true
}

async function onDownload(id: string) {
  try {
    await bundles.download(id)
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

function openExport() {
  if (!exportEnabled.value) {
    notify.push('先在顶部选择一个账号', 'warning')
    return
  }
  exportForm.id = `account-${bots.currentAccountId}`
  exportForm.name = `${accountName.value} 的配置`
  exportForm.version = '1.0.0'
  exportForm.description = ''
  exportOptions.value = []
  Object.keys(exportSelected).forEach((key) => delete exportSelected[key])
  exportDialog.value = true
  void loadExportOptions()
}

async function loadExportOptions() {
  try {
    const options = await bundles.loadExportOptions()
    exportOptions.value = options
    for (const option of options) exportSelected[option.name] = true
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

const exportModules = computed(() =>
  exportOptions.value.filter((option) => exportSelected[option.name]),
)

const exportModuleSize = computed(() =>
  exportModules.value.reduce((total, option) => total + (option.size || 0), 0),
)

function toggleAllExportModules(value: boolean) {
  for (const option of exportOptions.value) exportSelected[option.name] = value
}

async function doExport() {
  exporting.value = true
  try {
    await bundles.exportAccount({
      id: exportForm.id.trim() || `account-${bots.currentAccountId}`,
      name: exportForm.name.trim() || '账号配置',
      version: exportForm.version.trim() || '1.0.0',
      description: exportForm.description.trim(),
      modules: exportOptions.value.length
        ? exportModules.value.map((option) => option.name)
        : undefined,
    })
    notify.push('已导出配置包（密钥未包含）', 'success')
    exportDialog.value = false
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    exporting.value = false
  }
}

onMounted(async () => {
  if (!bots.bots.length) await bots.fetchBots()
  await bundles.load()
  await bundles.loadActivations()
})
</script>

<template>
  <div>
    <div class="app-page-header" style="align-items: center">
      <div>
        <h1 class="app-page-title">
          <v-icon icon="mdi-package-variant-closed" size="28" color="primary" class="mr-1" />
          配置包
        </h1>
        <div class="app-page-subtitle">把一套配置打包、分享到另一台电脑</div>
      </div>
      <div class="d-flex align-center gap-2">
        <v-btn variant="tonal" prepend-icon="mdi-export" :disabled="!exportEnabled" @click="openExport">
          导出当前账号
        </v-btn>
        <v-btn color="primary" prepend-icon="mdi-import" @click="onImport">导入配置包</v-btn>
      </div>
    </div>

    <v-alert type="info" variant="tonal" density="compact" class="mb-4">
      <div class="text-caption">
        导入后先检查包内容再保存；「应用到账号」会创建人格和模型、写入该账号配置，并自动安装随包插件，
        失败自动回滚。密钥不随包分发，导入后需要重新填写。
      </div>
      <div class="text-caption mt-1">
        要安装的是单个插件（插件包 .zip）？
        <v-btn size="x-small" variant="text" @click="router.push('/modules')">去功能插件</v-btn>
      </div>
    </v-alert>

    <v-progress-linear v-if="bundles.loading" indeterminate color="primary" class="mb-3" />

    <v-card v-if="!bundles.list.length && !bundles.loading" variant="outlined">
      <EmptyState
        icon="mdi-package-variant"
        title="还没有配置包"
        description="用右上角「导出当前账号」导出第一套，或「导入配置包」载入别人分享的 .zip。"
      />
    </v-card>

    <v-row v-else>
      <v-col v-for="item in bundles.list" :key="item.id" cols="12" md="6" xl="4">
        <v-card variant="outlined" class="bundle-card app-card-hover">
          <v-card-item>
            <template #prepend>
              <v-avatar color="lightprimary" size="40" rounded="lg">
                <v-icon icon="mdi-package-variant" color="primary" />
              </v-avatar>
            </template>
            <v-card-title class="text-body-1 font-weight-bold app-line-clamp-1">
              {{ item.name }}
              <v-chip size="x-small" variant="tonal" class="ml-1">v{{ item.version }}</v-chip>
            </v-card-title>
            <v-card-subtitle class="app-line-clamp-2">
              {{ item.description || '导入自本地文件' }}
            </v-card-subtitle>
          </v-card-item>
          <v-card-text class="text-caption" style="opacity: 0.7">
            <div>导入时间：{{ formatTime(item.imported_at) }}</div>
            <div>大小：{{ formatSize(item.size) }}</div>
            <div v-if="item.modules?.length">
              含插件 {{ item.modules.length }} 个
              <span v-if="item.modules_bundled">（随包 {{ item.modules_bundled }}）</span>
            </div>
          </v-card-text>
          <v-card-actions>
            <v-btn
              size="small"
              color="primary"
              variant="tonal"
              prepend-icon="mdi-play-circle-outline"
              :disabled="!exportEnabled"
              @click="openApply(item)"
            >
              应用到账号
            </v-btn>
            <v-btn size="small" variant="text" prepend-icon="mdi-download" @click="onDownload(item.id)">
              下载
            </v-btn>
            <v-spacer />
            <v-btn size="small" variant="text" color="error" prepend-icon="mdi-delete-outline" @click="onUninstall(item.id)">
              卸载
            </v-btn>
          </v-card-actions>
        </v-card>
      </v-col>
    </v-row>

    <v-card v-if="bundles.activations.length" variant="outlined" class="mt-4">
      <v-card-title class="text-body-1">
        <v-icon icon="mdi-history" class="mr-2" color="primary" /> 应用记录
      </v-card-title>
      <v-card-text>
        <div
          v-for="record in bundles.activations"
          :key="record.id"
          class="d-flex align-center flex-wrap gap-2 py-1"
        >
          <v-chip size="x-small" :color="statusColor(record.status)" variant="tonal">
            {{ statusText(record.status) }}
          </v-chip>
          <span class="text-body-2">{{ bundleName(record.bundle_id) }}</span>
          <span class="text-caption" style="opacity: 0.6">{{ formatTime(record.started_at) }}</span>
          <span v-if="record.error" class="text-caption text-error">{{ record.error }}</span>
          <v-spacer />
          <v-btn
            v-if="record.status === 'active'"
            size="x-small"
            variant="text"
            color="warning"
            prepend-icon="mdi-undo-variant"
            @click="onRollback(record.id)"
          >
            回滚
          </v-btn>
        </div>
      </v-card-text>
    </v-card>

    <BundleImportDialog v-model="importDialog" @imported="bundles.load()" />

    <v-dialog v-model="exportDialog" max-width="620">
      <v-card>
        <v-card-title class="d-flex align-center">
          <v-icon icon="mdi-export" class="mr-2" color="primary" /> 导出为配置包
        </v-card-title>
        <v-card-text>
          <div class="text-caption mb-3" style="opacity: 0.7">
            将从「{{ accountName }}」收集人格、模型、AI 角色配置与插件参数；第三方插件会连代码一起打包，
            内置插件只记录版本。密钥不会打进包。
          </div>
          <v-text-field v-model="exportForm.id" label="标识（可留空，自动生成）" density="comfortable" variant="outlined" />
          <v-text-field v-model="exportForm.name" label="名称" density="comfortable" variant="outlined" />
          <v-text-field v-model="exportForm.version" label="版本" density="comfortable" variant="outlined" />
          <v-text-field v-model="exportForm.description" label="描述（可选）" density="comfortable" variant="outlined" />

          <div class="d-flex align-center mt-2 mb-1">
            <div class="text-body-2 font-weight-medium">打包哪些插件</div>
            <v-spacer />
            <v-btn size="x-small" variant="text" @click="toggleAllExportModules(true)">全选</v-btn>
            <v-btn size="x-small" variant="text" @click="toggleAllExportModules(false)">全不选</v-btn>
          </div>
          <div class="text-caption mb-2" style="opacity: 0.65">
            已选 {{ exportModules.length }} 个；第三方插件代码约 {{ formatSize(exportModuleSize) }}。
            没选的插件不会进包（目标账号保持原状）。
          </div>
          <v-card variant="tonal" class="module-picker">
            <v-card-text class="py-1">
              <div v-if="!exportOptions.length" class="text-caption py-2">正在读取插件清单…</div>
              <div
                v-for="option in exportOptions"
                :key="option.name"
                class="d-flex align-center"
              >
                <v-checkbox
                  v-model="exportSelected[option.name]"
                  :label="option.display_name || option.name"
                  density="compact"
                  hide-details
                  class="flex-grow-1"
                />
                <v-chip size="x-small" variant="text">v{{ option.version || '—' }}</v-chip>
                <v-chip size="x-small" variant="text" class="ml-1">
                  {{ option.builtin ? '内置' : '第三方' }}
                </v-chip>
                <span v-if="!option.builtin" class="text-caption ml-2" style="opacity: 0.65">
                  {{ formatSize(option.size) }}
                </span>
              </div>
            </v-card-text>
          </v-card>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="exportDialog = false">取消</v-btn>
          <v-btn color="primary" variant="tonal" :loading="exporting" @click="doExport">导出</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog v-model="applyDialog" max-width="620">
      <v-card>
        <v-card-title class="d-flex align-center">
          <v-icon icon="mdi-play-circle-outline" class="mr-2" color="primary" />
          应用到「{{ accountName }}」
        </v-card-title>
        <v-card-text>
          <template v-if="!applyResult">
            <v-alert type="info" variant="tonal" density="compact" class="mb-3">
              <div class="text-caption">
                应用会创建人格和模型、写入该账号配置，并自动安装随包插件；失败会自动回滚，不会留下半套配置。
                应用后可在「应用记录」里一键撤销。密钥不随包分发：目标账号已填过的同名密钥会复用，
                还缺哪个会在这里提示。
              </div>
            </v-alert>
            <div class="text-body-2 mb-2">
              {{ applyTarget?.name }} <span style="opacity: 0.6">v{{ applyTarget?.version }}</span>
            </div>
            <v-select
              v-if="reusePresetOptions.length"
              v-model="reusePresetId"
              :items="reusePresetOptions"
              item-title="label"
              item-value="id"
              label="复用本机已保存的模型服务密钥（可选）"
              clearable
              density="comfortable"
              variant="outlined"
              class="mb-1"
            />
            <div
              v-if="reusePresetId"
              class="text-caption mb-2"
              style="opacity: 0.7"
            >
              已选择复用：密钥不用再手填，也不会写进包。
            </div>
            <v-text-field
              v-for="secret in applyTarget?.secrets || []"
              :key="secret.key"
              v-model="applySecrets[secret.key]"
              :label="secret.label"
              :placeholder="secret.required ? '必填（不随包分发）' : '可选'"
              :disabled="Boolean(reusePresetId) && secret.key === 'providers[0].api_key'"
              type="password"
              density="comfortable"
              variant="outlined"
              autocomplete="off"
            />
            <v-checkbox
              v-model="applyInstallDeps"
              density="compact"
              hide-details
              class="mt-2"
              label="应用后自动安装插件缺失依赖（需要联网）"
            />
            <v-text-field
              v-if="applyInstallDeps"
              v-model="applyDepsMirror"
              label="pip 镜像地址（可选）"
              placeholder="https://pypi.tuna.tsinghua.edu.cn/simple"
              density="compact"
              variant="outlined"
              hide-details
              class="mt-1"
            />
          </template>
          <v-alert v-else type="success" variant="tonal" density="compact">
            已应用到「{{ accountName }}」：
            新建 {{ applyResult.plan.creates.length }} 项、更新 {{ applyResult.plan.updates.length }} 处。
            <div v-if="applyResult.plan.reused_provider_preset_id">
              密钥复用了本机已保存的模型服务
            </div>
            <template v-if="applyResult.modules_installed?.length">
              <div>已安装随包插件：{{ applyResult.modules_installed.join('、') }}</div>
            </template>
            <template v-if="applyResult.modules_upgraded?.length">
              <div>已升级随包插件：{{ applyResult.modules_upgraded.join('、') }}</div>
            </template>
            <template v-if="applyResult.modules_deps?.length">
              <div class="mt-1">插件依赖：</div>
              <div v-for="item in applyResult.modules_deps" :key="item.module" class="text-caption">
                {{ item.module }}：{{ depsLine(item) }}
              </div>
            </template>
            <template v-if="applyResult.health">
              <div class="mt-1">
                自检：
                <v-chip
                  size="x-small"
                  :color="applyResult.health.ok ? 'success' : 'warning'"
                  variant="tonal"
                >
                  {{ applyResult.health.ok ? '已就绪' : '有待确认项' }}
                </v-chip>
              </div>
              <div
                v-for="mod in applyResult.health.modules"
                :key="mod.name"
                class="d-flex align-center flex-wrap gap-2 text-caption"
              >
                <span>插件 {{ mod.name }}：{{ mod.loaded && !mod.error ? '已加载' : '未加载' }}</span>
                <v-btn
                  v-if="!mod.loaded || mod.error"
                  size="x-small"
                  variant="text"
                  prepend-icon="mdi-package-variant"
                  @click="openDeps(mod.name)"
                >
                  检查依赖
                </v-btn>
              </div>
              <div
                v-if="!applyResult.health.agent.persona || !applyResult.health.agent.provider_preset || !applyResult.health.agent.provider_model"
                class="text-caption"
              >
                人格、模型服务或模型还没选好，请到「AI 对话面板」检查后再使用。
              </div>
            </template>
          </v-alert>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="applyDialog = false">关闭</v-btn>
          <v-btn
            v-if="!applyResult"
            color="primary"
            variant="tonal"
            :loading="bundles.applying"
            @click="doApply"
          >
            确认应用
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <ConfirmDialog
      v-model="uninstallDialog"
      :title="`卸载「${uninstallTarget?.name || ''}」`"
      message="会撤销它应用到当前账号的改动，并删除导入的配置包文件（其它账号还在用的会保留）。"
      :details="['当前账号的配置恢复到应用前', '删除配置包文件（其它账号仍在用的会保留）', '可勾选下方选项保留随包安装的插件']"
      danger
      confirm-text="确认卸载"
      :loading="uninstalling"
      @confirm="doUninstall"
    >
      <v-checkbox v-model="keepModules" density="compact" hide-details label="保留随包安装的插件（只撤销配置）" class="mt-2" />
    </ConfirmDialog>

    <ConfirmDialog
      v-model="rollbackOpen"
      title="回滚这次应用"
      message="应用时创建的对象会被删除，账号配置恢复到应用前。"
      :details="['删除这次应用创建的人格与模型', '该账号配置恢复原状', '已卸载的配置包不受影响']"
      danger
      confirm-text="回滚"
      :loading="rollbackBusy"
      @confirm="doRollback"
    />

    <PluginDepsDialog
      v-model="depsOpen"
      :module-name="depsModuleName"
      :module-label="depsModuleLabel"
      @done="onDepsDone"
    />
  </div>
</template>

<style scoped>
.bundle-card {
  height: 100%;
  border-radius: 14px;
}

.module-picker {
  max-height: 220px;
  overflow-y: auto;
}
</style>
