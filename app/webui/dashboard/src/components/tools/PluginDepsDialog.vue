<script setup lang="ts">
/**
 * 插件依赖安装对话框：查询缺失组件 → 确认 → 安装 →（成功后）提示重新加载。
 *
 * 抽成共享组件，供「功能插件」列表、插件配置页、配置包导入/自检复用。
 * 只处理**已安装插件**（后端接口要求先装插件）；zip 预检入口先装插件再打开本对话框。
 */
import { computed, ref, watch } from 'vue'
import http, { errorMessage } from '@/api/http'
import { useNotifyStore } from '@/stores/notify'

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    /** 插件内部名（接口参数），例如 llm_tools_extra */
    moduleName: string
    /** 插件显示名（给用户看） */
    moduleLabel?: string
  }>(),
  {
    moduleLabel: '',
  },
)

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  /** 安装结束（含成功/失败），调用方据此刷新列表 */
  done: [payload: { loaded: boolean }]
  /** 依赖本来就齐全：调用方直接重新加载插件 */
  reload: []
}>()

const notify = useNotifyStore()

interface DependencyStatus {
  requirements?: string | null
  declared?: string[]
  rejected?: string[]
  missing?: string[]
  command?: string[]
}

interface DependencyInstallResult {
  loaded?: boolean
}

const open = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
})

const loading = ref(false)
const loadError = ref('')
const packages = ref<string[]>([])
const command = ref<string[]>([])
const rejected = ref<string[]>([])
const mirror = ref('')
const running = ref(false)
const error = ref('')
const errorHint = ref('')
const errorCommand = ref<string[]>([])
const errorOutput = ref('')

const title = computed(() => props.moduleLabel || props.moduleName || '这个插件')

function asTextList(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : []
}

/** 失败输出只留末尾一段，避免把整屏终端日志糊到对话框里 */
function outputTail(text: string): string {
  const limit = 1500
  return text.length > limit ? `…${text.slice(-limit)}` : text
}

function resetFeedback() {
  error.value = ''
  errorHint.value = ''
  errorCommand.value = []
  errorOutput.value = ''
  loadError.value = ''
}

function close() {
  if (running.value) return
  open.value = false
}

async function loadStatus() {
  if (!props.moduleName) return
  loading.value = true
  loadError.value = ''
  try {
    const res = await http.get<DependencyStatus>(
      `/api/modules/${encodeURIComponent(props.moduleName)}/dependencies`,
    )
    const status = res.data || {}
    packages.value = asTextList(status.missing)
    command.value = asTextList(status.command)
    rejected.value = asTextList(status.rejected)
  } catch (err) {
    loadError.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}

function failureDetail(err: unknown): { message: string; hint: string; command: string[]; output: string } {
  const body = ((err as { response?: { data?: Record<string, any> } })?.response?.data ||
    {}) as Record<string, any>
  const message = typeof body.message === 'string' && body.message ? body.message : errorMessage(err)
  const hint = typeof body.hint === 'string' ? body.hint : ''
  const stdout = typeof body.stdout === 'string' ? body.stdout : ''
  const stderr = typeof body.stderr === 'string' ? body.stderr : ''
  const output = [stdout, stderr]
    .map((part) => part.trim())
    .filter(Boolean)
    .join('\n')
  return { message, hint, command: asTextList(body.command), output: outputTail(output) }
}

async function runInstall() {
  if (!props.moduleName || running.value) return
  running.value = true
  error.value = ''
  errorHint.value = ''
  errorCommand.value = []
  errorOutput.value = ''
  try {
    const form = new FormData()
    form.append('confirm_deps', 'true')
    const trimmed = mirror.value.trim()
    if (trimmed) form.append('mirror', trimmed)
    const res = await http.post<DependencyInstallResult>(
      `/api/modules/${encodeURIComponent(props.moduleName)}/install-deps`,
      form,
    )
    const loaded = res.data?.loaded !== false
    emit('done', { loaded })
    open.value = false
    notify.push(
      loaded ? '组件已安装，插件已重新加载' : '组件已安装，插件还没加载成功，请重新加载',
      loaded ? 'success' : 'warning',
    )
  } catch (err) {
    const failure = failureDetail(err)
    error.value = failure.message
    errorHint.value = failure.hint
    errorCommand.value = failure.command
    errorOutput.value = failure.output
  } finally {
    running.value = false
  }
}

async function copyCommand(list: string[]) {
  const text = list.join(' ')
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
    notify.push('命令已复制', 'success')
  } catch {
    notify.push('复制失败，请手动复制命令', 'warning')
  }
}

watch(
  () => props.modelValue,
  (v) => {
    if (!v) return
    resetFeedback()
    packages.value = []
    command.value = []
    rejected.value = []
    mirror.value = ''
    void loadStatus()
  },
)
</script>

<template>
  <v-dialog v-model="open" max-width="640" :persistent="running" @keydown.esc="close">
    <v-card>
      <v-card-title class="d-flex align-center">
        <v-icon icon="mdi-package-variant" class="mr-2" color="primary" />
        安装运行组件
        <v-spacer />
        <v-btn icon="mdi-close" variant="text" size="small" :disabled="running" @click="close" />
      </v-card-title>
      <v-card-text>
        <div class="text-caption mb-2">插件：{{ title }}</div>

        <v-progress-linear v-if="loading" indeterminate color="primary" class="mb-3" />

        <v-alert v-if="loadError" type="error" variant="tonal" density="compact" class="mb-3">
          <div class="text-caption">{{ loadError }}</div>
          <v-btn size="small" variant="text" prepend-icon="mdi-refresh" class="mt-1" @click="loadStatus">
            重试
          </v-btn>
        </v-alert>

        <template v-if="!loading && !loadError">
          <v-alert v-if="rejected.length" type="warning" variant="tonal" density="compact" class="mb-3">
            <div class="text-caption">清单里有无法自动安装的写法，请让作者改成「包名 + 版本」再试。</div>
          </v-alert>
          <template v-else-if="!packages.length">
            <v-alert type="success" variant="tonal" density="compact" class="mb-3">
              <div class="text-caption">组件已就绪，点「重新加载」即可。</div>
            </v-alert>
            <v-btn size="small" variant="tonal" prepend-icon="mdi-refresh" @click="emit('reload'); close()">
              重新加载
            </v-btn>
          </template>
          <template v-else>
            <div class="text-caption mb-1">缺少运行组件：</div>
            <div class="d-flex flex-wrap gap-1 mb-3">
              <v-chip v-for="dep in packages" :key="dep" size="small" variant="tonal">{{ dep }}</v-chip>
            </div>
            <div v-if="command.length" class="mb-3">
              <div class="text-caption mb-1">将要执行的命令</div>
              <pre class="deps-command">{{ command.join(' ') }}</pre>
              <v-btn size="x-small" variant="text" prepend-icon="mdi-content-copy" @click="copyCommand(command)">
                复制命令
              </v-btn>
            </div>
            <v-text-field
              v-model="mirror"
              label="镜像地址（可选）"
              placeholder="留空使用默认源"
              density="comfortable"
              variant="outlined"
              hide-details
              :disabled="running"
              class="mb-2"
            />
          </template>
        </template>

        <v-progress-linear v-if="running" indeterminate color="primary" class="mt-3" />

        <v-alert v-if="error" type="error" variant="tonal" density="compact" class="mt-3">
          <div class="text-caption">{{ error }}</div>
          <div v-if="errorHint" class="text-caption mt-1">{{ errorHint }}</div>
          <div v-if="errorCommand.length" class="mt-2">
            <div class="text-caption mb-1">也可以自己在终端执行：</div>
            <pre class="deps-command">{{ errorCommand.join(' ') }}</pre>
            <v-btn size="x-small" variant="text" prepend-icon="mdi-content-copy" @click="copyCommand(errorCommand)">
              复制命令
            </v-btn>
          </div>
          <v-expansion-panels v-if="errorOutput" variant="accordion" flat class="mt-2">
            <v-expansion-panel title="查看安装输出">
              <v-expansion-panel-text>
                <pre class="deps-command">{{ errorOutput }}</pre>
              </v-expansion-panel-text>
            </v-expansion-panel>
          </v-expansion-panels>
        </v-alert>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn variant="text" :disabled="running" @click="close">取消</v-btn>
        <v-btn
          v-if="packages.length && !rejected.length"
          color="primary"
          variant="tonal"
          :loading="running"
          :disabled="running"
          @click="runInstall"
        >
          安装组件
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
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
