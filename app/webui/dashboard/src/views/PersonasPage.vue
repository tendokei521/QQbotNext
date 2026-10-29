<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import http, { errorMessage } from '@/api/http'
import { useAgentConfigStore } from '@/stores/agentConfig'
import { useNotifyStore } from '@/stores/notify'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'

interface RolePreset {
  id: string
  name: string
  description: string
  system_prompt: string
  created_at: number
  updated_at: number
}

const agent = useAgentConfigStore()
const notify = useNotifyStore()
const router = useRouter()

const presets = ref<RolePreset[]>([])
const loading = ref(false)
const saving = ref(false)
const search = ref('')
const selectedId = ref('')
const editingId = ref('')
const form = reactive({
  name: '',
  description: '',
  system_prompt: '',
})

const EMPTY_FORM = { name: '', description: '', system_prompt: '' }
/** 表单快照：用来判断是否有未保存改动（防止「新建/另存」静默覆盖草稿） */
const formBaseline = ref({ ...EMPTY_FORM })
const discardDialog = ref(false)
const formDirty = computed(
  () =>
    form.name !== formBaseline.value.name ||
    form.description !== formBaseline.value.description ||
    form.system_prompt !== formBaseline.value.system_prompt,
)

function snapshotForm() {
  formBaseline.value = {
    name: form.name,
    description: form.description,
    system_prompt: form.system_prompt,
  }
}

const filteredPresets = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return presets.value
  return presets.value.filter((p) =>
    [p.name, p.description, p.system_prompt].some((v) => (v || '').toLowerCase().includes(q)),
  )
})

const selectedPreset = computed(() => presets.value.find((p) => p.id === selectedId.value) || null)

function fillForm(p: RolePreset) {
  Object.assign(form, {
    name: p.name,
    description: p.description,
    system_prompt: p.system_prompt,
  })
  snapshotForm()
}

function openPreset(p: RolePreset) {
  selectedId.value = p.id
  editingId.value = p.id
  fillForm(p)
}

/** 真正清空表单（不弹确认）；「新建/另存」内部复用 */
function resetForm() {
  selectedId.value = ''
  editingId.value = ''
  Object.assign(form, EMPTY_FORM)
  snapshotForm()
}

/** 确认放弃草稿后的后续动作：null = 只新建；'save-as' = 继续把当前绑定人格另存 */
const pendingSaveAs = ref(false)

function startNew() {
  // 有未保存内容时先确认，避免点一下「新建」就把草稿静默丢掉
  pendingSaveAs.value = false
  if (formDirty.value) {
    discardDialog.value = true
    return
  }
  resetForm()
}

function confirmDiscardDraft() {
  discardDialog.value = false
  const continueSaveAs = pendingSaveAs.value
  pendingSaveAs.value = false
  resetForm()
  if (continueSaveAs) loadBoundPresetForCopy()
}

async function loadPresets() {
  loading.value = true
  try {
    const res = await http.get<{ ok: boolean; presets: RolePreset[] }>('/api/role-presets')
    presets.value = res.data?.presets || []
    if (selectedId.value && !presets.value.some((p) => p.id === selectedId.value)) {
      selectedId.value = ''
      editingId.value = ''
    }
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    loading.value = false
  }
}

async function savePreset() {
  if (!form.name.trim()) {
    notify.push('请填写人格名称', 'warning')
    return
  }
  if (!form.system_prompt.trim()) {
    notify.push('请填写人格提示词', 'warning')
    return
  }
  saving.value = true
  try {
    const payload = {
      name: form.name.trim(),
      description: form.description.trim(),
      system_prompt: form.system_prompt,
    }
    let saved: RolePreset | undefined
    if (editingId.value) {
      const res = await http.put<{ ok: boolean; preset: RolePreset }>(`/api/role-presets/${editingId.value}`, payload)
      saved = res.data?.preset
      notify.push('人格预设已更新', 'success')
    } else {
      const res = await http.post<{ ok: boolean; preset: RolePreset }>('/api/role-presets', payload)
      saved = res.data?.preset
      notify.push('人格预设已创建', 'success')
    }
    await loadPresets()
    if (saved?.id) {
      selectedId.value = saved.id
      editingId.value = saved.id
      fillForm(saved)
    }
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    saving.value = false
  }
}

/** 删除确认：统一 ConfirmDialog（与删号/卸载插件同一套样式与影响清单） */
const deleteTarget = ref<RolePreset | null>(null)
const deleting = ref(false)

function askDelete(p: RolePreset) {
  deleteTarget.value = p
}

async function confirmDelete() {
  const p = deleteTarget.value
  if (!p) return
  deleting.value = true
  try {
    await http.delete(`/api/role-presets/${p.id}`)
    notify.push('人格预设已删除', 'success')
    if (selectedId.value === p.id) {
      selectedId.value = ''
      editingId.value = ''
    }
    deleteTarget.value = null
    await loadPresets()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    deleting.value = false
  }
}

/**
 * 「应用人格」的唯一入口在 AI 对话面板：人格设定页只负责编辑预设内容，
 * 不再在这里写 system_prompt / persona_preset_id，避免两条写路径悄悄分叉。
 */
function goToPanel() {
  router.push('/ai')
}

function saveCurrentAsPreset() {
  if (agent.botId == null) {
    notify.push('请先在「账号管理」添加账号，才能从当前账号另存', 'warning')
    return
  }
  // system_prompt 是退役字段：当前人格只认 persona_preset_id
  if (!boundPersonaPreset()) {
    notify.push('当前账号还没绑定人格预设', 'warning')
    return
  }
  if (formDirty.value) {
    pendingSaveAs.value = true
    discardDialog.value = true
    return
  }
  loadBoundPresetForCopy()
}

/** 当前账号活引用的人格预设（读 persona_preset_id，不读退役的 system_prompt） */
function boundPersonaPreset(): RolePreset | null {
  const presetId = String(agent.draft.persona_preset_id || '')
  if (!presetId) return null
  return presets.value.find((p) => p.id === presetId) || null
}

/** 把绑定的人格内容载入表单，作为「另存为新人格」的起点 */
function loadBoundPresetForCopy() {
  const source = boundPersonaPreset()
  if (!source) {
    notify.push('当前账号还没绑定人格预设', 'warning')
    return
  }
  resetForm()
  Object.assign(form, {
    name: `${source.name} 副本`,
    description: source.description,
    system_prompt: source.system_prompt,
  })
  snapshotForm()
  notify.push(`已载入「${source.name}」的内容，填写名称后保存`, 'info')
}

onMounted(() => {
  agent.load()
  loadPresets()
})
</script>

<template>
  <div>
    <div class="app-page-header" style="align-items: center">
      <div>
        <h1 class="app-page-title">
          <v-icon icon="mdi-account-heart" class="mr-1" color="pink" />
          人格设定
        </h1>
        <div class="app-page-subtitle">管理可复用的人格预设，在 AI 对话面板里选用</div>
      </div>
      <v-spacer />
      <v-btn color="primary" variant="tonal" prepend-icon="mdi-plus" @click="startNew">新建人格</v-btn>
      <v-btn variant="tonal" prepend-icon="mdi-refresh" @click="loadPresets">刷新</v-btn>
    </div>

    <!-- 人格是全局资产：没有账号也能全部编辑；只有「应用」这类动作需要账号 -->
    <v-alert v-if="agent.botId == null" type="info" variant="tonal" density="compact" class="mt-2 mb-3">
      <div class="text-caption">
        当前没有选定账号：人格可以照常<strong>新建、编辑、保存</strong>；
        「从当前账号另存」需要先在「账号管理」添加账号。
      </div>
    </v-alert>

    <v-row class="mt-2">
      <v-col cols="12" md="4">
        <v-card variant="outlined" class="h-full">
          <v-card-title class="d-flex align-center">
            <v-icon icon="mdi-account-group-outline" class="mr-2" color="pink" /> 人格预设
            <v-spacer />
          </v-card-title>
          <v-card-text class="pa-2">
            <v-text-field
              v-model="search"
              prepend-inner-icon="mdi-magnify"
              label="搜索人格"
              density="compact"
              variant="outlined"
              hide-details
              class="mb-2"
            />
            <v-progress-linear v-if="loading" indeterminate color="primary" />
            <template v-else-if="!presets.length">
              <div class="text-caption text-center pa-4">暂无人格预设，点右上角「新建人格」开始</div>
            </template>
            <v-list v-else density="compact">
              <v-list-item
                v-for="p in filteredPresets"
                :key="p.id"
                :active="selectedId === p.id"
                @click="openPreset(p)"
              >
                <template #prepend>
                  <v-icon icon="mdi-account-heart" color="pink" />
                </template>
                <v-list-item-title class="text-body-2">{{ p.name }}</v-list-item-title>
                <v-list-item-subtitle class="text-caption">
                  {{ p.description || p.system_prompt.slice(0, 30) || '无描述' }}
                </v-list-item-subtitle>
                <template #append>
                  <v-btn
                    size="x-small"
                    variant="text"
                    icon="mdi-delete"
                    color="error"
                    title="删除人格"
                    @click.stop="askDelete(p)"
                  />
                </template>
              </v-list-item>
              <v-list-item v-if="!filteredPresets.length">
                <v-list-item-title class="text-caption text-center py-3" style="opacity: 0.5">
                  无匹配人格
                </v-list-item-title>
              </v-list-item>
            </v-list>
          </v-card-text>
        </v-card>
      </v-col>

      <v-col cols="12" md="8">
        <v-card variant="outlined" class="mb-4">
          <v-card-title class="d-flex align-center">
            <v-icon icon="mdi-pencil-outline" class="mr-2" color="primary" />
            {{ editingId ? '编辑人格' : '新建人格' }}
            <v-spacer />
            <v-chip v-if="selectedPreset" size="small" variant="tonal" color="pink">
              {{ selectedPreset.name }}
            </v-chip>
          </v-card-title>
          <v-card-text>
            <v-row>
              <v-col cols="12" md="6">
                <v-text-field
                  v-model="form.name"
                  label="人格名称"
                  density="comfortable"
                  variant="outlined"
                  hide-details
                  placeholder="例如：元气少女"
                />
              </v-col>
              <v-col cols="12" md="6">
                <v-text-field
                  v-model="form.description"
                  label="人格描述（一句话）"
                  density="comfortable"
                  variant="outlined"
                  hide-details
                />
              </v-col>
            </v-row>
            <v-textarea
              v-model="form.system_prompt"
              label="人格提示词"
              hint="描述它是什么样的人、怎么说话；写得具体一些效果更稳"
              persistent-hint
              placeholder="例如：你是一个混在群里的普通群友，说话简短自然，不端着"
              auto-grow
              rows="14"
              variant="outlined"
              class="mt-3"
              spellcheck="false"
            />
            <div class="d-flex flex-wrap gap-3 mt-3">
              <v-btn
                color="primary"
                variant="tonal"
                prepend-icon="mdi-content-save"
                :loading="saving"
                @click="savePreset"
              >
                保存人格
              </v-btn>
              <v-btn
                variant="tonal"
                color="pink"
                prepend-icon="mdi-arrow-right-bold-circle-outline"
                :disabled="!selectedPreset"
                @click="goToPanel"
              >
                去 AI 对话面板选用
              </v-btn>
              <v-btn
                variant="tonal"
                prepend-icon="mdi-account-plus-outline"
                :disabled="agent.botId == null"
                @click="saveCurrentAsPreset"
              >
                从当前账号另存为人格
              </v-btn>
            </div>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <ConfirmDialog
      :model-value="deleteTarget !== null"
      title="删除人格预设"
      danger
      confirm-text="删除"
      :loading="deleting"
      :message="`「${deleteTarget?.name || ''}」删除后不可恢复。`"
      :details="[
        '已经应用它的人格提示词会保留在该账号里，不会自动改动',
        '其它账号如果引用了同一个人格预设，会回退到内置默认人格',
      ]"
      @update:model-value="(v: boolean) => { if (!v) deleteTarget = null }"
      @confirm="confirmDelete"
    />

    <!-- 新建/另存前有未保存内容：先确认，不要静默丢掉草稿 -->
    <ConfirmDialog
      v-model="discardDialog"
      title="放弃未保存的修改？"
      message="当前表单里还有没保存的内容，新建会清空它们。"
      danger
      confirm-text="放弃修改"
      cancel-text="继续编辑"
      @confirm="confirmDiscardDraft"
    />
  </div>
</template>