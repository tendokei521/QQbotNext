<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import http, { errorMessage } from '@/api/http'
import { useNotifyStore } from '@/stores/notify'
import { useWebuiStore } from '@/stores/webui'

interface ConfigProfile {
  id: string
  name: string
  config: Record<string, any>
  updated_at: number
}

const notify = useNotifyStore()
const webui = useWebuiStore()
const enabled = computed(() => !!webui.config.experimental?.show_experimental)
const profiles = ref<ConfigProfile[]>([])
const routes = ref<Record<string, string>>({})
const loading = ref(false)
const saving = ref(false)

const dialog = ref(false)
const editingId = ref('')
const form = reactive({
  name: '',
  configText: '{}',
})

const routeDialog = ref(false)
const routeForm = reactive({
  umo: '',
  profile_id: '',
})

function fmtTime(ts: number | undefined | null): string {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false })
}

async function load() {
  loading.value = true
  try {
    const [profilesRes, routesRes] = await Promise.all([
      http.get<{ ok: boolean; profiles: ConfigProfile[] }>('/api/config-profiles'),
      http.get<{ ok: boolean; routes: Record<string, string> }>('/api/config-profiles/routes/all'),
    ])
    profiles.value = profilesRes.data?.profiles || []
    routes.value = routesRes.data?.routes || {}
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editingId.value = ''
  Object.assign(form, { name: '', configText: '{}' })
  dialog.value = true
}

function openEdit(profile: ConfigProfile) {
  editingId.value = profile.id
  Object.assign(form, {
    name: profile.name,
    configText: JSON.stringify(profile.config || {}, null, 2),
  })
  dialog.value = true
}

async function save() {
  if (!form.name.trim()) {
    notify.push('请填写档案名称', 'warning')
    return
  }
  let config: Record<string, any> = {}
  try {
    config = JSON.parse(form.configText || '{}')
  } catch {
    notify.push('配置 JSON 格式不正确', 'error')
    return
  }
  saving.value = true
  try {
    const payload = { name: form.name.trim(), config }
    if (editingId.value) {
      await http.put(`/api/config-profiles/${editingId.value}`, payload)
      notify.push('配置档案已更新', 'success')
    } else {
      await http.post('/api/config-profiles', payload)
      notify.push('配置档案已创建', 'success')
    }
    dialog.value = false
    await load()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    saving.value = false
  }
}

async function deleteProfile(profile: ConfigProfile) {
  if (!window.confirm(`确认删除配置档案「${profile.name}」？相关路由会一并清理。`)) return
  try {
    await http.delete(`/api/config-profiles/${profile.id}`)
    notify.push('配置档案已删除', 'success')
    await load()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

function openRouteDialog() {
  Object.assign(routeForm, { umo: '', profile_id: profiles.value[0]?.id || '' })
  routeDialog.value = true
}

async function saveRoute() {
  if (!routeForm.umo.trim() || !routeForm.profile_id) {
    notify.push('请填写 UMO 与档案', 'warning')
    return
  }
  try {
    await http.put('/api/config-profiles/routes', routeForm)
    notify.push('路由已设置', 'success')
    routeDialog.value = false
    await load()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

async function deleteRoute(umo: string) {
  try {
    await http.delete(`/api/config-profiles/routes/${encodeURIComponent(umo)}`)
    notify.push('路由已删除', 'success')
    await load()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

onMounted(load)
</script>

<template>
  <div v-if="enabled">
    <div class="app-page-header" style="align-items: center">
      <div>
        <h1 class="app-page-title">配置档案</h1>
        <div class="app-page-subtitle">把整套 Agent / Provider 选择保存为档案，并按群 / 私聊路由绑定</div>
      </div>
      <v-spacer />
      <v-btn color="primary" variant="tonal" prepend-icon="mdi-plus" @click="openCreate">新建档案</v-btn>
    </div>

    <v-progress-linear v-if="loading" indeterminate color="primary" />

    <v-row class="mt-2">
      <v-col cols="12" md="7">
        <v-card variant="outlined">
          <v-card-title class="d-flex align-center">
            <v-icon icon="mdi-book-multiple" class="mr-2" color="primary" /> 配置档案
          </v-card-title>
          <v-table density="comfortable">
            <thead>
              <tr>
                <th>名称</th>
                <th>配置项数</th>
                <th>更新时间</th>
                <th class="text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="profiles.length === 0">
                <td colspan="4" class="text-center text-caption pa-6">暂无配置档案</td>
              </tr>
              <tr v-for="p in profiles" :key="p.id">
                <td class="font-weight-medium">{{ p.name }}</td>
                <td class="text-caption">{{ Object.keys(p.config || {}).length }}</td>
                <td class="text-caption">{{ fmtTime(p.updated_at) }}</td>
                <td class="text-right">
                  <v-btn size="x-small" variant="text" color="primary" @click="openEdit(p)">编辑</v-btn>
                  <v-btn size="x-small" variant="text" color="error" @click="deleteProfile(p)">删除</v-btn>
                </td>
              </tr>
            </tbody>
          </v-table>
        </v-card>
      </v-col>

      <v-col cols="12" md="5">
        <v-card variant="outlined">
          <v-card-title class="d-flex align-center">
            <v-icon icon="mdi-route" class="mr-2" color="primary" /> 路由绑定
            <v-spacer />
            <v-btn size="small" color="primary" variant="tonal" prepend-icon="mdi-plus" @click="openRouteDialog">添加路由</v-btn>
          </v-card-title>
          <v-table density="comfortable">
            <thead>
              <tr>
                <th>UMO</th>
                <th>档案</th>
                <th class="text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="Object.keys(routes).length === 0">
                <td colspan="3" class="text-center text-caption pa-6">暂无路由</td>
              </tr>
              <tr v-for="(profileId, umo) in routes" :key="umo">
                <td class="text-caption">{{ umo }}</td>
                <td class="text-caption">{{ profiles.find((p) => p.id === profileId)?.name || profileId }}</td>
                <td class="text-right">
                  <v-btn size="x-small" variant="text" color="error" @click="deleteRoute(umo)">删除</v-btn>
                </td>
              </tr>
            </tbody>
          </v-table>
          <v-card-text class="text-caption" style="color: rgba(var(--v-theme-on-surface), 0.55)">
            UMO 格式：group_群号 / private_QQ号
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <v-dialog v-model="dialog" max-width="600">
      <v-card>
        <v-card-title class="d-flex align-center">
          <v-icon icon="mdi-book-multiple" class="mr-2" color="primary" />
          {{ editingId ? '编辑配置档案' : '新建配置档案' }}
        </v-card-title>
        <v-card-text>
          <v-text-field v-model="form.name" label="名称" density="comfortable" hide-details class="mb-3" />
          <v-textarea
            v-model="form.configText"
            label="配置 JSON（Agent 配置覆盖项）"
            rows="12"
            spellcheck="false"
            class="config-json-editor"
          />
          <div class="text-caption mt-1" style="color: rgba(var(--v-theme-on-surface), 0.55)">
            示例：{ "provider_model_id": "模型ID", "system_prompt": "你是...", "temperature": 0.8 }
          </div>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="dialog = false">取消</v-btn>
          <v-btn color="primary" variant="tonal" :loading="saving" @click="save">保存</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog v-model="routeDialog" max-width="420">
      <v-card>
        <v-card-title class="d-flex align-center">
          <v-icon icon="mdi-route" class="mr-2" color="primary" /> 添加路由
        </v-card-title>
        <v-card-text>
          <v-text-field v-model="routeForm.umo" label="UMO" placeholder="group_123456 / private_10001" density="comfortable" hide-details class="mb-3" />
          <v-select
            v-model="routeForm.profile_id"
            :items="profiles.map((p) => ({ title: p.name, value: p.id }))"
            label="配置档案"
            density="comfortable"
            hide-details
          />
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="routeDialog = false">取消</v-btn>
          <v-btn color="primary" variant="tonal" @click="saveRoute">保存</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
  <div v-else class="text-center pa-10 text-caption" style="color: rgba(var(--v-theme-on-surface), 0.55)">
    这是实验性功能，请在「设置 → 实验性选项」中开启「显示实验性选项」
  </div>
</template>

<style scoped>
.config-json-editor :deep(textarea) {
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 12.5px;
}
</style>