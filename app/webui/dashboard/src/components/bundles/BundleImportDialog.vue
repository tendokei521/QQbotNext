<script setup lang="ts">
/**
 * 导入配置包：选文件 → 预检报告 → 一键「导入并应用」或「仅导入」。
 *
 * 预检是"信任界面"：把「包含什么 / 缺什么 / 需要你填什么 / 会安装什么」讲清楚再装。
 * 选中的账号会应用配置并安装随包插件；未选账号时只把配置包存到列表。
 */
import { computed, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { displayName, useBotsStore } from '@/stores/bots'
import {
  useBundlesStore,
  type ApplyResult,
  type BundleHealth,
  type BundleReport,
  type ModuleDepsLog,
  type ReusePreset,
} from '@/stores/bundles'
import { useNotifyStore } from '@/stores/notify'
import { errorMessage } from '@/api/http'
import PluginDepsDialog from '@/components/tools/PluginDepsDialog.vue'

defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'imported'): void
}>()

const bots = useBotsStore()
const bundles = useBundlesStore()
const notify = useNotifyStore()
const router = useRouter()

// ---- 缺少运行组件：复用插件的依赖安装对话框 ----
const depsOpen = ref(false)
const depsModuleName = ref('')
const depsModuleLabel = ref('')

function openDeps(moduleName: string, label?: string) {
  depsModuleName.value = moduleName
  depsModuleLabel.value = label || moduleName
  depsOpen.value = true
}

function goPluginInstall() {
  close()
  router.push('/modules')
}

type Screen = 'pick' | 'preview' | 'result'
const screen = ref<Screen>('pick')
const file = ref<File | null>(null)
const report = ref<BundleReport | null>(null)
const result = ref<{ bundle: { name: string; version: string } } | null>(null)
const applied = ref<ApplyResult | null>(null)
const health = ref<BundleHealth | null>(null)
const importedOnly = ref(false)
const acceptCode = ref(false)
/** 应用后自动安装随包模块的 pip 依赖（需联网；有缺失依赖时默认勾选） */
const installDeps = ref(false)
/** 可选 pip 镜像地址（留空用默认源/已配置镜像） */
const depsMirror = ref('')
const error = ref('')
const dragOver = ref(false)
const secretsForm = reactive<Record<string, string>>({})
const reusePresets = ref<ReusePreset[]>([])
const reusePresetId = ref<string | null>(null)

const targetAccountId = computed(() => bots.currentAccountId)
const reusePresetOptions = computed(() =>
  reusePresets.value.map((preset) => ({
    id: preset.id,
    label: `${preset.name}${preset.config?.api_base ? ` · ${preset.config.api_base}` : ''}`,
  })),
)
/** 包含可执行插件代码 → 必须显式确认信任才允许导入/应用 */
const needsConsent = computed(() => Boolean(report.value?.requires_code_consent))
const consentBlocked = computed(() => needsConsent.value && !acceptCode.value)
/** 随包模块缺的第三方依赖（模块名 + 依赖行）：勾选后可联网一键补齐 */
const missingDeps = computed(() =>
  (report.value?.modules || []).flatMap((mod) =>
    (mod.missing_requirements || []).map((dep) => ({ module: mod.name, dep })),
  ),
)
const hasMissingDeps = computed(() => missingDeps.value.length > 0)
const accountLabel = computed(() => {
  const bot = bots.bots.find((item) => item.id === targetAccountId.value)
  return bot ? displayName(bot) : (targetAccountId.value || '未选择账号')
})

function reset() {
  screen.value = 'pick'
  file.value = null
  report.value = null
  result.value = null
  applied.value = null
  health.value = null
  importedOnly.value = false
  acceptCode.value = false
  installDeps.value = false
  depsMirror.value = ''
  reusePresetId.value = null
  error.value = ''
  dragOver.value = false
  Object.keys(secretsForm).forEach((key) => delete secretsForm[key])
}

function close() {
  emit('update:modelValue', false)
  reset()
}

async function prepare(picked: File) {
  if (!picked.name.toLowerCase().endsWith('.zip')) {
    error.value = '仅支持 .zip 配置包'
    return
  }
  file.value = picked
  error.value = ''
  acceptCode.value = false
  try {
    report.value = await bundles.validateFile(picked)
    Object.keys(secretsForm).forEach((key) => delete secretsForm[key])
    for (const secret of report.value.secrets || []) secretsForm[secret.key] = ''
    reusePresetId.value = null
    installDeps.value = hasMissingDeps.value
    depsMirror.value = ''
    if (targetAccountId.value) {
      try {
        reusePresets.value = await bundles.loadReusePresets()
      } catch (err) {
        reusePresets.value = []
      }
    }
    screen.value = 'preview'
  } catch (err) {
    error.value = errorMessage(err)
  }
}

async function onPick(event: Event) {
  const picked = (event.target as HTMLInputElement).files?.[0]
  if (picked) await prepare(picked)
}

async function onDrop(event: DragEvent) {
  dragOver.value = false
  const picked = event.dataTransfer?.files?.[0]
  if (picked) await prepare(picked)
}

async function confirmImport() {
  if (!file.value) return
  try {
    const response = await bundles.importFile(file.value, acceptCode.value)
    result.value = { bundle: response.bundle }
    applied.value = null
    importedOnly.value = true
    screen.value = 'result'
    notify.push('配置包已导入（未应用）', 'success')
    emit('imported')
  } catch (err) {
    error.value = errorMessage(err)
    notify.push(error.value, 'error')
  }
}

async function confirmApply() {
  if (!file.value || !targetAccountId.value) return
  error.value = ''
  try {
    const response = await bundles.importAndApply(
      file.value,
      targetAccountId.value,
      { ...secretsForm },
      acceptCode.value,
      reusePresetId.value || '',
      installDeps.value,
      depsMirror.value,
    )
    result.value = { bundle: response.bundle }
    applied.value = response.apply || null
    health.value = response.health || null
    importedOnly.value = false
    screen.value = 'result'
    notify.push('配置包已导入并应用到账号', 'success')
    emit('imported')
  } catch (err: any) {
    const data = err?.response?.data
    if (data?.imported && data?.bundle) {
      // 配置包已存到列表、应用失败：切到结果页说明，用户可补密钥后到列表重试
      result.value = { bundle: data.bundle }
      applied.value = null
      importedOnly.value = true
      screen.value = 'result'
      emit('imported')
    }
    error.value = errorMessage(err)
    notify.push(error.value, 'error')
  }
}

function moduleActionText(action: string): string {
  return ({
    install: '将安装',
    upgrade: '将升级',
    skip: '已满足',
    missing: '缺失',
    config_only: '只写配置',
  } as Record<string, string>)[action] || action
}

function moduleActionColor(action: string): string {
  return ({
    install: 'info',
    upgrade: 'warning',
    skip: 'success',
    missing: 'error',
    config_only: 'default',
  } as Record<string, string>)[action] || 'default'
}

function formatSize(bytes: number): string {
  if (!bytes) return '—'
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

/** 依赖安装结果压成一行：已装了什么 / 还缺什么（避免把 pip 输出糊满对话框） */
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
</script>

<template>
  <v-dialog :model-value="modelValue" max-width="720" @update:model-value="(v: boolean) => !v && close()">
    <v-card>
      <v-card-title class="d-flex align-center">
        <v-icon icon="mdi-package-variant" class="mr-2" color="primary" />
        {{ screen === 'pick' ? '导入配置包' : screen === 'preview' ? '包内容检查' : '导入完成' }}
        <v-spacer />
        <v-btn icon="mdi-close" variant="text" size="small" @click="close" />
      </v-card-title>

      <v-card-text>
        <v-alert v-if="error" type="error" variant="tonal" density="compact" class="mb-3">
          {{ error }}
        </v-alert>

        <!-- 选择文件 -->
        <template v-if="screen === 'pick'">
          <div
            class="drop-zone"
            :class="{ 'drop-zone--over': dragOver }"
            @dragover.prevent="dragOver = true"
            @dragleave.prevent="dragOver = false"
            @drop.prevent="onDrop"
          >
            <v-icon icon="mdi-cloud-upload-outline" size="40" color="primary" />
            <div class="text-body-2 mt-2">把 .zip 配置包拖到这里</div>
            <v-btn size="small" variant="tonal" class="mt-2" @click="($refs.fileInput as HTMLInputElement)?.click()">
              选择文件
            </v-btn>
            <input ref="fileInput" type="file" accept=".zip" hidden @change="onPick" />
          </div>
          <div class="text-caption mt-2" style="opacity: 0.65">
            要安装的是单个插件（插件包 .zip）？
            <v-btn size="x-small" variant="text" @click="goPluginInstall">去功能插件</v-btn>
          </div>
          <v-progress-linear v-if="bundles.validating" indeterminate color="primary" class="mt-3" />
        </template>

        <!-- 预检报告 -->
        <template v-else-if="screen === 'preview' && report">
          <div class="d-flex align-center flex-wrap gap-2 mb-2">
            <span class="text-h6">{{ report.bundle.name }}</span>
            <v-chip size="x-small" variant="tonal">v{{ report.bundle.version }}</v-chip>
            <v-chip size="x-small" variant="tonal">{{ formatSize(report.summary.size) }}</v-chip>
          </div>
          <div class="text-caption mb-3" style="opacity: 0.7">{{ report.bundle.description }}</div>

          <div class="d-flex flex-wrap gap-2 mb-3">
            <v-chip size="small" variant="tonal">插件 {{ report.summary.modules }}</v-chip>
            <v-chip
              v-if="report.summary.modules_bundled"
              size="small"
              variant="tonal"
              color="primary"
            >
              随包插件 {{ report.summary.modules_bundled }}
            </v-chip>
            <v-chip
              v-if="report.summary.modules_to_install"
              size="small"
              variant="tonal"
              color="info"
            >
              将安装 {{ report.summary.modules_to_install }}
            </v-chip>
            <v-chip size="small" variant="tonal">模板 {{ report.summary.presets }}</v-chip>
            <v-chip size="small" variant="tonal">设置项 {{ report.summary.agent_keys }}</v-chip>
            <v-chip size="small" variant="tonal" :color="report.summary.secrets_required ? 'warning' : undefined">
              需填密钥 {{ report.summary.secrets_required }}
            </v-chip>
          </div>

          <v-alert
            v-for="issue in report.issues"
            :key="issue.code + issue.title"
            :type="issue.level === 'error' ? 'error' : 'warning'"
            variant="tonal"
            density="compact"
            class="mb-2"
          >
            <div class="text-body-2">{{ issue.title }}</div>
            <div v-if="issue.detail" class="text-caption">{{ issue.detail }}</div>
            <div v-if="issue.fix" class="text-caption" style="opacity: 0.7">建议：{{ issue.fix }}</div>
          </v-alert>

          <v-card v-if="report.modules?.length" variant="tonal" class="mt-2">
            <v-card-text>
              <div class="text-caption font-weight-medium mb-1">插件清单</div>
              <div
                v-for="mod in report.modules"
                :key="mod.name"
                class="d-flex align-center flex-wrap gap-2 py-1 text-caption"
              >
                <span class="font-weight-medium">{{ mod.name }}</span>
                <v-chip
                  v-if="mod.bundled"
                  size="x-small"
                  color="primary"
                  variant="tonal"
                >
                  随包 {{ mod.package_version || '' }}
                  <template v-if="mod.package_size"> · {{ formatSize(mod.package_size) }}</template>
                </v-chip>
                <v-chip v-else-if="mod.installed_version" size="x-small" variant="text">
                  已装 v{{ mod.installed_version }}
                </v-chip>
                <v-chip v-else size="x-small" variant="text">未附带</v-chip>
                <v-chip size="x-small" :color="moduleActionColor(mod.action)" variant="tonal">
                  {{ moduleActionText(mod.action) }}
                </v-chip>
                <v-chip
                  v-if="mod.missing_requirements?.length"
                  size="x-small"
                  color="warning"
                  variant="tonal"
                >
                  缺依赖 {{ mod.missing_requirements.length }}
                </v-chip>
              </div>
              <div
                v-for="mod in report.modules"
                :key="mod.name + '-deps'"
              >
                <div
                  v-if="mod.missing_requirements?.length"
                  class="d-flex align-center flex-wrap gap-2 text-caption"
                >
                  <span class="text-error">
                    {{ mod.name }} 缺少：{{ mod.missing_requirements.join('、') }}
                  </span>
                  <v-btn
                    v-if="mod.installed_version"
                    size="x-small"
                    variant="text"
                    prepend-icon="mdi-package-variant"
                    @click="openDeps(mod.name)"
                  >
                    安装组件
                  </v-btn>
                  <span v-else style="opacity: 0.7">应用完成后可补装</span>
                </div>
              </div>
            </v-card-text>
          </v-card>

          <v-card v-if="hasMissingDeps" variant="tonal" color="warning" class="mt-2">
            <v-card-text class="text-caption">
              <div class="font-weight-medium mb-1">
                目标环境缺少 {{ missingDeps.length }} 个插件依赖
              </div>
              <div>应用时可联网用 pip 自动安装；不勾选则需稍后到插件页手动安装。</div>
              <div class="d-flex flex-wrap gap-1 mt-2">
                <v-chip
                  v-for="item in missingDeps"
                  :key="item.module + item.dep"
                  size="x-small"
                  variant="tonal"
                >
                  {{ item.module }} · {{ item.dep }}
                </v-chip>
              </div>
              <v-checkbox
                v-model="installDeps"
                density="compact"
                hide-details
                class="mt-1"
                label="应用后自动安装缺失依赖（需要联网）"
              />
              <v-text-field
                v-if="installDeps"
                v-model="depsMirror"
                label="pip 镜像地址（可选）"
                placeholder="https://pypi.tuna.tsinghua.edu.cn/simple"
                density="compact"
                variant="outlined"
                hide-details
                class="mt-1"
              />
            </v-card-text>
          </v-card>

          <v-card v-if="needsConsent" variant="tonal" color="warning" class="mt-2">
            <v-card-text class="text-caption">
              <div class="font-weight-medium mb-1">此配置包包含可执行的插件代码</div>
              <div>
                应用时会从包内安装并加载
                {{ report.summary.modules_bundled }} 个第三方插件，请确认来源可信后再继续。
              </div>
              <v-checkbox
                v-model="acceptCode"
                density="compact"
                hide-details
                label="我信任此配置包包含的插件代码"
                class="mt-1"
              />
            </v-card-text>
          </v-card>

          <v-card v-if="targetAccountId && reusePresetOptions.length" variant="tonal" class="mt-2">
            <v-card-text>
              <div class="text-caption font-weight-medium mb-1">复用本机已保存的模型服务密钥（可选）</div>
              <v-select
                v-model="reusePresetId"
                :items="reusePresetOptions"
                item-title="label"
                item-value="id"
                clearable
                density="comfortable"
                variant="outlined"
                hide-details
              />
              <div class="text-caption mt-1" style="opacity: 0.7">
                选一个已连通的模型服务：密钥不用手填，也不会写进包。
              </div>
            </v-card-text>
          </v-card>

          <v-card v-if="report.secrets.length" variant="tonal" class="mt-2">
            <v-card-text>
              <div class="text-caption font-weight-medium mb-2">
                密钥（不随包分发；目标账号已填过同名密钥的可以留空）
              </div>
              <v-text-field
                v-for="secret in report.secrets"
                :key="secret.key"
                v-model="secretsForm[secret.key]"
                :label="secret.label + (secret.required ? ' *' : '')"
                :placeholder="secret.required ? '必填（不随包分发）' : '可选'"
                :disabled="Boolean(reusePresetId) && secret.key === 'providers[0].api_key'"
                type="password"
                density="comfortable"
                variant="outlined"
                autocomplete="off"
                hide-details
                class="mb-2"
              />
            </v-card-text>
          </v-card>

          <v-alert
            v-if="!targetAccountId"
            type="info"
            variant="tonal"
            density="compact"
            class="mt-2"
          >
            <div class="text-caption">
              未选择账号：这次只把配置包存到列表里，不会写入账号配置。到顶部选一个账号后，可在列表里「应用到账号」。
            </div>
          </v-alert>
        </template>

        <!-- 结果 -->
        <template v-else-if="screen === 'result' && result">
          <v-alert v-if="applied" type="success" variant="tonal" density="compact">
            已导入「{{ result.bundle.name }}」v{{ result.bundle.version }} 并应用到「{{ accountLabel }}」：
            新建 {{ applied.plan.creates.length }} 项、更新 {{ applied.plan.updates.length }} 处。
            <template v-if="applied.modules_installed?.length">
              <div>已安装随包插件：{{ applied.modules_installed.join('、') }}</div>
            </template>
            <template v-if="applied.modules_upgraded?.length">
              <div>已升级随包插件：{{ applied.modules_upgraded.join('、') }}</div>
            </template>
            <template v-if="applied.modules_deps?.length">
              <div class="mt-1">插件依赖：</div>
              <div v-for="item in applied.modules_deps" :key="item.module" class="text-caption">
                {{ item.module }}：{{ depsLine(item) }}
              </div>
            </template>
            <template v-if="health">
              <div class="mt-1">
                自检：
                <v-chip size="x-small" :color="health.ok ? 'success' : 'warning'" variant="tonal">
                  {{ health.ok ? '已就绪' : '有待确认项' }}
                </v-chip>
              </div>
              <div
                v-for="mod in health.modules"
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
            </template>
          </v-alert>
          <v-alert v-else type="warning" variant="tonal" density="compact">
            已导入「{{ result.bundle.name }}」v{{ result.bundle.version }}，但还没应用。
            到配置包列表里点「应用到账号」即可写入配置、创建人格和模型并安装随包插件（可撤销）。
          </v-alert>
        </template>
      </v-card-text>

      <v-card-actions>
        <v-btn variant="text" @click="screen === 'preview' ? (screen = 'pick') : close()">
          {{ screen === 'preview' ? '重新选择' : '取消' }}
        </v-btn>
        <v-spacer />
        <template v-if="screen === 'preview'">
          <v-btn
            variant="text"
            :loading="bundles.importing && !bundles.applying"
            :disabled="!report?.ok || consentBlocked"
            @click="confirmImport"
          >
            仅导入
          </v-btn>
          <v-btn
            v-if="targetAccountId"
            color="primary"
            variant="tonal"
            prepend-icon="mdi-play-circle-outline"
            :loading="bundles.importing"
            :disabled="!report?.ok || consentBlocked"
            @click="confirmApply"
          >
            导入并应用到「{{ accountLabel }}」
          </v-btn>
          <v-btn
            v-else
            color="primary"
            variant="tonal"
            :disabled="!report?.ok || consentBlocked"
            @click="confirmImport"
          >
            导入（暂不应用）
          </v-btn>
        </template>
        <v-btn v-else-if="screen === 'result'" color="primary" @click="close">完成</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>

  <PluginDepsDialog
    v-model="depsOpen"
    :module-name="depsModuleName"
    :module-label="depsModuleLabel"
    @done="emit('imported')"
  />
</template>

<style scoped>
.drop-zone {
  border: 1px dashed rgba(var(--v-theme-on-surface), 0.25);
  border-radius: 12px;
  padding: 24px 16px;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.drop-zone--over {
  border-color: rgb(var(--v-theme-primary));
  background: rgba(var(--v-theme-primary), 0.06);
}
</style>
