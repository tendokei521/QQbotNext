<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useModulesStore, type PermissionConfig } from '@/stores/modules'
import { useWebuiStore } from '@/stores/webui'
import { useBotsStore } from '@/stores/bots'
import { useNotifyStore } from '@/stores/notify'
import { errorMessage } from '@/api/http'
import http from '@/api/http'
import ConfigForm from '@/components/config/ConfigForm.vue'
import PermissionEditor from '@/components/config/PermissionEditor.vue'

const route = useRoute()
const router = useRouter()
const modules = useModulesStore()
const webui = useWebuiStore()
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
  everyone: '所有人',
  member: '成员',
  group_admin: '管理员',
  group_owner: '群主',
  owner: '所有者',
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
  saveStatus.value = 'saving'
  try {
    await modules.saveConfig(name, { ...draft })
    dirtyFlag = false
    saveStatus.value = 'clean'
    saveMsg.value = `已保存 ${new Date().toLocaleTimeString('zh-CN', { hour12: false })}`
  } catch (err) {
    saveStatus.value = 'error'
    notify.push(errorMessage(err), 'error')
  }
}

function onPermissionChange(v: PermissionConfig) {
  permission.value = v
  permDirtyFlag = true
  if (permTimer) clearTimeout(permTimer)
  permTimer = window.setTimeout(doSavePermission, 2000)
}

async function doSavePermission() {
  if (permTimer) {
    clearTimeout(permTimer)
    permTimer = null
  }
  if (!permDirtyFlag) return
  try {
    await modules.savePermission(name, permission.value)
    permDirtyFlag = false
    notify.push('权限已保存', 'success')
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

// ---------- 启用开关 ----------
async function onToggleEnabled(v: boolean | null) {
  if (!mod.value) return
  try {
    await modules.toggle(name, !!v)
    notify.push(`模块 ${mod.value.name} 已${v ? '启用' : '禁用'}`, 'success')
  } catch (err) {
    notify.push(errorMessage(err), 'error')
    if (mod.value) mod.value.enabled = !v
  }
}

// ---------- 单一服务 ----------
const ssOn = computed(() => !!webui.config.single_service[name])
const affectedGroups = ref<{ gid: string; name: string }[]>([])
const groupsLoaded = ref(false)

async function loadGroups() {
  try {
    const res = await http.get<{
      bots_groups: Record<string, { bot_id: number | null; index: number; groups: number[]; groups_info: { group_id?: number; group_name?: string }[] }>
    }>('/api/bots/groups')
    const map = new Map<string, { name: string; botIndexes: number[] }>()
    Object.values(res.data?.bots_groups || {}).forEach((bg) => {
      const infos = bg.groups_info || []
      bg.groups.forEach((gid, i) => {
        const key = String(gid)
        const row = map.get(key) || { name: String(infos[i]?.group_name ?? gid), botIndexes: [] }
        row.botIndexes.push(bg.index)
        map.set(key, row)
      })
    })
    groupsLoaded.value = true
    const svc = webui.config.multi_group.groups || {}
    affectedGroups.value = [...map.entries()]
      .filter(([gid]) => {
        const cfg = svc[gid]
        if (!cfg || cfg.service_bot_index === undefined) return false
        const row = map.get(gid)!
        return cfg.service_bot_index !== bots.currentIndex && row.botIndexes.length >= 2
      })
      .map(([gid, row]) => ({ gid, name: row.name }))
  } catch {
    groupsLoaded.value = false
  }
}

async function onToggleSingleService(v: boolean | null) {
  try {
    await webui.saveSingleService({ ...webui.config.single_service, [name]: !!v })
    notify.push(`单一服务模式已${v ? '启用' : '关闭'}`, 'success')
    if (v) loadGroups()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

const showSingleServiceWarning = computed(
  () => ssOn.value && groupsLoaded.value && affectedGroups.value.length > 0,
)

// ---------- 插件自定义页 ----------
const pluginSrc = computed(() => {
  const base = `/api/module/${name}/page`
  const botId = bots.currentBot?.bot_id ?? null
  return botId !== null ? `${base}?bot_id=${botId}` : base
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
  () => bots.currentIndex,
  () => {
    loadGroups()
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
  if (!mod.value) {
    await modules.load()
  }
  initFromModule()
  loadGroups()
  await ensureModule()
  await nextTick()
})
</script>

<template>
  <div v-if="mod">
    <div class="app-page-header" style="align-items: center">
      <div class="d-flex align-center gap-2 flex-wrap">
        <v-btn variant="text" icon="mdi-arrow-left" size="small" title="返回模块列表" @click="router.push('/modules')" />
        <h1 class="app-page-title">{{ mod.name }}</h1>
        <v-chip size="small" variant="tonal" color="primary">{{ mod.name_sign }}</v-chip>
        <v-chip size="small" variant="tonal" :color="PERMISSION_LABEL[mod.permission] ? 'info' : 'default'">
          {{ PERMISSION_LABEL[mod.permission] || mod.permission }}
        </v-chip>
        <v-chip v-if="mod.bot_id" size="small" variant="tonal">Bot {{ mod.bot_id }}</v-chip>
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

    <!-- 单一服务模式 -->
    <v-card variant="outlined" class="mb-4">
      <v-card-text class="d-flex align-center justify-space-between flex-wrap gap-2 py-3">
        <div class="d-flex align-center gap-2">
          <v-icon icon="mdi-server" color="purple" />
          <div>
            <div class="font-weight-medium">单一服务模式</div>
            <div class="text-caption">启用后仅由指定账号处理本模块的群消息</div>
          </div>
        </div>
        <v-switch :model-value="ssOn" color="purple" density="compact" hide-details @update:model-value="onToggleSingleService" />
      </v-card-text>
    </v-card>

    <v-alert
      v-if="showSingleServiceWarning"
      type="warning"
      variant="tonal"
      density="compact"
      class="mb-4"
    >
      <div class="text-caption">
        当前账号非本模块指定服务账号，该模块在部分群下不会触发：{{ affectedGroups.map((g) => g.name).join('、') }}
      </div>
    </v-alert>

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
          :bot-id="bots.currentBot?.bot_id ?? null"
          @change="onChange"
        />
      </v-card-text>
    </v-card>
  </div>

  <div v-else-if="modules.loading" class="empty-tip">
    <v-skeleton-loader type="card-heading, article, actions" class="rounded-xl w-100" style="max-width: 900px" />
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
</style>
