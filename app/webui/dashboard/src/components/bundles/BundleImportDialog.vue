<script setup lang="ts">
/**
 * 导入预设：选文件 → 预检报告 → 一键「导入并应用」或「仅导入」。
 *
 * 预检是"信任界面"：把「包含什么 / 缺什么 / 需要你填什么 / 会安装什么」讲清楚再装。
 * 选中的账号会应用配置并安装随包模块；未选账号时只登记工件。
 */
import { computed, reactive, ref } from 'vue'
import { displayName, useBotsStore } from '@/stores/bots'
import { useBundlesStore, type ApplyResult, type BundleReport } from '@/stores/bundles'
import { useNotifyStore } from '@/stores/notify'
import { errorMessage } from '@/api/http'

defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'imported'): void
}>()

const bots = useBotsStore()
const bundles = useBundlesStore()
const notify = useNotifyStore()

type Screen = 'pick' | 'preview' | 'result'
const screen = ref<Screen>('pick')
const file = ref<File | null>(null)
const report = ref<BundleReport | null>(null)
const result = ref<{ bundle: { name: string; version: string } } | null>(null)
const applied = ref<ApplyResult | null>(null)
const importedOnly = ref(false)
const error = ref('')
const dragOver = ref(false)
const secretsForm = reactive<Record<string, string>>({})

const targetAccountId = computed(() => bots.currentAccountId)
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
  importedOnly.value = false
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
    error.value = '仅支持 .zip 预设包'
    return
  }
  file.value = picked
  error.value = ''
  try {
    report.value = await bundles.validateFile(picked)
    Object.keys(secretsForm).forEach((key) => delete secretsForm[key])
    for (const secret of report.value.secrets || []) secretsForm[secret.key] = ''
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
    const response = await bundles.importFile(file.value)
    result.value = { bundle: response.bundle }
    applied.value = null
    importedOnly.value = true
    screen.value = 'result'
    notify.push('预设已导入（未应用）', 'success')
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
    )
    result.value = { bundle: response.bundle }
    applied.value = response.apply || null
    importedOnly.value = false
    screen.value = 'result'
    notify.push('预设已导入并应用到账号', 'success')
    emit('imported')
  } catch (err: any) {
    const data = err?.response?.data
    if (data?.imported && data?.bundle) {
      // 工件已登记、应用失败：切到结果页说明，用户可补密钥后到列表重试
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

function formatSize(bytes: number): string {
  if (!bytes) return '—'
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
</script>

<template>
  <v-dialog :model-value="modelValue" max-width="720" @update:model-value="(v: boolean) => !v && close()">
    <v-card>
      <v-card-title class="d-flex align-center">
        <v-icon icon="mdi-package-variant" class="mr-2" color="primary" />
        {{ screen === 'pick' ? '导入预设' : screen === 'preview' ? '预检报告' : '导入完成' }}
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
            <div class="text-body-2 mt-2">把 .zip 预设包拖到这里</div>
            <v-btn size="small" variant="tonal" class="mt-2" @click="($refs.fileInput as HTMLInputElement)?.click()">
              选择文件
            </v-btn>
            <input ref="fileInput" type="file" accept=".zip" hidden @change="onPick" />
          </div>
          <v-progress-linear v-if="bundles.validating" indeterminate color="primary" class="mt-3" />
        </template>

        <!-- 预检报告 -->
        <template v-else-if="screen === 'preview' && report">
          <div class="d-flex align-center flex-wrap gap-2 mb-2">
            <span class="text-h6">{{ report.bundle.name }}</span>
            <v-chip size="x-small" variant="tonal">v{{ report.bundle.version }}</v-chip>
            <v-chip size="x-small" variant="text">{{ report.bundle.id }}</v-chip>
            <v-chip size="x-small" variant="tonal">{{ formatSize(report.summary.size) }}</v-chip>
          </div>
          <div class="text-caption mb-3" style="opacity: 0.7">{{ report.bundle.description }}</div>

          <div class="d-flex flex-wrap gap-2 mb-3">
            <v-chip size="small" variant="tonal">模块 {{ report.summary.modules }}</v-chip>
            <v-chip
              v-if="report.summary.modules_bundled"
              size="small"
              variant="tonal"
              color="primary"
            >
              随包模块 {{ report.summary.modules_bundled }}
            </v-chip>
            <v-chip
              v-if="report.summary.modules_to_install"
              size="small"
              variant="tonal"
              color="info"
            >
              将安装 {{ report.summary.modules_to_install }}
            </v-chip>
            <v-chip size="small" variant="tonal">预设 {{ report.summary.presets }}</v-chip>
            <v-chip size="small" variant="tonal">配置 {{ report.summary.agent_keys }}</v-chip>
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
              未选择账号：这次只会登记工件。到顶部选一个账号后，可在列表里「应用到账号」。
            </div>
          </v-alert>
        </template>

        <!-- 结果 -->
        <template v-else-if="screen === 'result' && result">
          <v-alert v-if="applied" type="success" variant="tonal" density="compact">
            已导入「{{ result.bundle.name }}」v{{ result.bundle.version }} 并应用到「{{ accountLabel }}」：
            新建 {{ applied.plan.creates.length }} 项、更新 {{ applied.plan.updates.length }} 处。
            <template v-if="applied.modules_installed?.length">
              <div>已安装随包模块：{{ applied.modules_installed.join('、') }}</div>
            </template>
            <template v-if="applied.modules_upgraded?.length">
              <div>已升级随包模块：{{ applied.modules_upgraded.join('、') }}</div>
            </template>
          </v-alert>
          <v-alert v-else type="warning" variant="tonal" density="compact">
            已导入「{{ result.bundle.name }}」v{{ result.bundle.version }}，但尚未应用。
            到列表里点「应用到账号」即可写入配置、创建人格/模型并安装随包模块（可回滚）。
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
            :disabled="!report?.ok"
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
            :disabled="!report?.ok"
            @click="confirmApply"
          >
            导入并应用到「{{ accountLabel }}」
          </v-btn>
          <v-btn v-else color="primary" variant="tonal" :disabled="!report?.ok" @click="confirmImport">
            导入（暂不应用）
          </v-btn>
        </template>
        <v-btn v-else-if="screen === 'result'" color="primary" @click="close">完成</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
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
