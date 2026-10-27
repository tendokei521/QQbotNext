<script setup lang="ts">
/**
 * 预设中心：导出一个账号的可迁移配置（人格卡 / 模型池 / Agent 配置 / 模块参数与启停 /
 * 第三方模块代码），并把它应用到目标账号（事务写 + 快照，失败自动回滚）。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { displayName, useBotsStore } from '@/stores/bots'
import { useBundlesStore, type ExportModuleOption } from '@/stores/bundles'
import { useNotifyStore } from '@/stores/notify'
import { errorMessage } from '@/api/http'
import EmptyState from '@/components/EmptyState.vue'
import BundleImportDialog from '@/components/bundles/BundleImportDialog.vue'

const bots = useBotsStore()
const bundles = useBundlesStore()
const notify = useNotifyStore()

const importDialog = ref(false)
const exportDialog = ref(false)
const exporting = ref(false)
const exportOptions = ref<ExportModuleOption[]>([])
const exportSelected = reactive<Record<string, boolean>>({})
const exportForm = reactive({
  id: '',
  name: '',
  version: '1.0.0',
  description: '',
})

// ---- 应用到账号（P3） ----
const applyDialog = ref(false)
const applyTarget = ref<{ id: string; name: string; version: string; secrets?: { key: string; label: string; required: boolean }[] } | null>(null)
const applySecrets = reactive<Record<string, string>>({})
const applyResult = ref<{
  activation_id: string
  plan: { creates: any[]; updates: any[] }
  modules_installed?: string[]
  modules_upgraded?: string[]
} | null>(null)

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

function openApply(item: { id: string; name: string; version: string; secrets?: { key: string; label: string; required: boolean }[] }) {
  if (!exportEnabled.value) {
    notify.push('先在顶部选择一个账号', 'warning')
    return
  }
  applyTarget.value = item
  applyResult.value = null
  Object.keys(applySecrets).forEach((key) => delete applySecrets[key])
  for (const secret of item.secrets || []) applySecrets[secret.key] = ''
  applyDialog.value = true
}

async function doApply() {
  if (!applyTarget.value || !bots.currentAccountId) return
  // 必填校验交给后端：目标账号可能已填过同名密钥（升级场景会复用），前端不该先拦。
  try {
    applyResult.value = await bundles.applyBundle(
      applyTarget.value.id,
      bots.currentAccountId,
      { ...applySecrets },
    )
    notify.push('已应用到当前账号', 'success')
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

async function onUninstall(bundleId: string) {
  if (!window.confirm('卸载这个预设？会回滚它应用到当前账号的改动，并删除工件文件。')) return
  try {
    await bundles.uninstallBundle(bundleId)
    notify.push('预设已卸载', 'success')
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

async function onRollback(activationId: string) {
  if (!window.confirm('回滚这次应用？应用时创建的对象会被删除，账号配置恢复到应用前。')) return
  try {
    await bundles.rollbackActivation(activationId)
    notify.push('已回滚', 'success')
  } catch (err) {
    notify.push(errorMessage(err), 'error')
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
    notify.push('已导出预设（密钥未包含）', 'success')
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
          预设中心
        </h1>
        <div class="app-page-subtitle">把一套配置打包、分享、换机迁移</div>
      </div>
      <div class="d-flex align-center gap-2">
        <v-btn variant="tonal" prepend-icon="mdi-export" :disabled="!exportEnabled" @click="openExport">
          导出当前账号
        </v-btn>
        <v-btn color="primary" prepend-icon="mdi-import" @click="onImport">导入预设</v-btn>
      </div>
    </div>

    <v-alert type="info" variant="tonal" density="compact" class="mb-4">
      <div class="text-caption">
        导入后先预检再登记工件；「应用到账号」会创建人格/模型、写入该账号配置，并自动安装随包模块，
        失败自动回滚。密钥不会随包分发，导入后需要你重新填写。
      </div>
    </v-alert>

    <v-progress-linear v-if="bundles.loading" indeterminate color="primary" class="mb-3" />

    <v-card v-if="!bundles.list.length && !bundles.loading" variant="outlined">
      <EmptyState
        icon="mdi-package-variant"
        title="还没有预设"
        description="可以从当前账号导出第一套，或导入别人分享的 .zip 预设包。"
      >
        <v-btn color="primary" variant="tonal" prepend-icon="mdi-import" @click="onImport">导入预设</v-btn>
      </EmptyState>
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
              含模块 {{ item.modules.length }} 个
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
          <span class="text-body-2">{{ record.bundle_id }}</span>
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
          <v-icon icon="mdi-export" class="mr-2" color="primary" /> 导出为预设
        </v-card-title>
        <v-card-text>
          <div class="text-caption mb-3" style="opacity: 0.7">
            将从「{{ accountName }}」收集人格卡、模型池、Agent 配置与模块参数；第三方模块代码会随包，
            内置模块只记录版本。密钥不包含在包内。
          </div>
          <v-text-field v-model="exportForm.id" label="预设 ID" density="comfortable" variant="outlined" />
          <v-text-field v-model="exportForm.name" label="名称" density="comfortable" variant="outlined" />
          <v-text-field v-model="exportForm.version" label="版本" density="comfortable" variant="outlined" />
          <v-text-field v-model="exportForm.description" label="描述（可选）" density="comfortable" variant="outlined" />

          <div class="d-flex align-center mt-2 mb-1">
            <div class="text-body-2 font-weight-medium">打包哪些模块</div>
            <v-spacer />
            <v-btn size="x-small" variant="text" @click="toggleAllExportModules(true)">全选</v-btn>
            <v-btn size="x-small" variant="text" @click="toggleAllExportModules(false)">全不选</v-btn>
          </div>
          <div class="text-caption mb-2" style="opacity: 0.65">
            已选 {{ exportModules.length }} 个；第三方模块代码约 {{ formatSize(exportModuleSize) }}。
            没选的模块不会进包（目标账号保持原状）。
          </div>
          <v-card variant="tonal" class="module-picker">
            <v-card-text class="py-1">
              <div v-if="!exportOptions.length" class="text-caption py-2">正在读取模块清单…</div>
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
                应用会创建人格/模型、写入该账号配置，并自动安装随包模块；失败自动回滚，不留半套配置。
                应用后可在「应用记录」里一键回滚。密钥不随包分发：目标账号已填过的同名密钥会复用，
                缺哪个由后端在应用前点名提示。
              </div>
            </v-alert>
            <div class="text-body-2 mb-2">
              {{ applyTarget?.name }} <span style="opacity: 0.6">v{{ applyTarget?.version }}</span>
            </div>
            <v-text-field
              v-for="secret in applyTarget?.secrets || []"
              :key="secret.key"
              v-model="applySecrets[secret.key]"
              :label="secret.label"
              :placeholder="secret.required ? '必填（不随包分发）' : '可选'"
              type="password"
              density="comfortable"
              variant="outlined"
              autocomplete="off"
            />
          </template>
          <v-alert v-else type="success" variant="tonal" density="compact">
            已应用（激活 {{ applyResult.activation_id }}）：
            新建 {{ applyResult.plan.creates.length }} 项、更新 {{ applyResult.plan.updates.length }} 处。
            <template v-if="applyResult.modules_installed?.length">
              <div>已安装随包模块：{{ applyResult.modules_installed.join('、') }}</div>
            </template>
            <template v-if="applyResult.modules_upgraded?.length">
              <div>已升级随包模块：{{ applyResult.modules_upgraded.join('、') }}</div>
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
