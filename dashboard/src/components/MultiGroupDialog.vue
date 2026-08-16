<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import http, { errorMessage } from '@/api/http'
import { useWebuiStore } from '@/stores/webui'
import { useNotifyStore } from '@/stores/notify'

interface GroupRow {
  gid: string
  name: string
  botIndexes: number[]
}

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void }>()

const webui = useWebuiStore()
const notify = useNotifyStore()

const rows = ref<GroupRow[]>([])
const serviceMap = ref<Record<string, string>>({})
const loading = ref(false)
const error = ref('')

const showAll = computed(() => webui.config.multi_group.show_all)

const visibleRows = computed(() => {
  const list = rows.value
  if (showAll.value) return [...list].sort((a, b) => b.botIndexes.length - a.botIndexes.length)
  return list.filter((r) => r.botIndexes.length >= 2).sort((a, b) => b.botIndexes.length - a.botIndexes.length)
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await http.get<{ bots_groups: Record<string, { bot_id: number | null; index: number; groups: number[]; groups_info: { group_id?: number; group_name?: string }[] }> }>('/api/bots/groups')
    const botsGroups = res.data?.bots_groups || {}

    // 汇总所有群：gid -> 出现过的 bot index 列表 + 群名
    const map = new Map<string, GroupRow>()
    Object.values(botsGroups).forEach((bg) => {
      const infos = bg.groups_info || []
      bg.groups.forEach((gid, i) => {
        const key = String(gid)
        const row = map.get(key) || { gid: key, name: String(infos[i]?.group_name ?? gid), botIndexes: [] }
        row.botIndexes.push(bg.index)
        if (infos[i]?.group_name && !row.name) row.name = String(infos[i].group_name)
        map.set(key, row)
      })
    })
    rows.value = [...map.values()]

    // 回填已存服务账号
    serviceMap.value = {}
    Object.entries(webui.config.multi_group.groups || {}).forEach(([gid, cfg]) => {
      if (cfg.service_bot_index !== undefined) serviceMap.value[gid] = String(cfg.service_bot_index)
    })
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}

async function onServiceChange(gid: string) {
  const groups = { ...webui.config.multi_group.groups }
  const v = serviceMap.value[gid]
  if (v !== undefined && v !== '') {
    groups[gid] = { service_bot_index: parseInt(v, 10) }
  } else {
    delete groups[gid]
  }
  webui.config.multi_group.groups = groups
  try {
    await webui.saveMultiGroup({ show_all: showAll.value, groups })
    notify.push('多群管理配置已保存', 'success')
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

async function onToggleShowAll() {
  webui.config.multi_group.show_all = !showAll.value
  try {
    await webui.saveMultiGroup({ show_all: showAll.value, groups: webui.config.multi_group.groups })
    notify.push('已更新展示范围', 'success')
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

watch(
  () => props.modelValue,
  (v) => {
    if (v) load()
  },
)
</script>

<template>
  <v-dialog :model-value="modelValue" @update:model-value="(v: boolean) => emit('update:modelValue', v)" max-width="720">
    <v-card>
      <v-card-title class="pa-4 pb-1">
        <v-icon icon="mdi-layers-triple-outline" class="mr-2" color="primary" /> 多群管理
      </v-card-title>
      <v-card-text>
        <v-checkbox :model-value="showAll" label="展示全部（包括仅单个账号在的群）" density="compact" hide-details color="primary" class="mb-2" @update:model-value="onToggleShowAll" />

        <div v-if="loading" class="hint"><v-progress-circular size="18" indeterminate /> 加载中…</div>
        <div v-else-if="error" class="hint error">{{ error }}</div>
        <div v-else class="group-list">
          <div v-if="visibleRows.length === 0" class="hint">暂无群数据</div>
          <div v-for="row in visibleRows" :key="row.gid" class="group-row">
            <div class="group-info">
              <div class="group-name">{{ row.name }}</div>
              <div class="group-meta">
                {{ row.gid }} · {{ row.botIndexes.length }} 个账号
                <v-chip size="x-small" variant="tonal" class="ml-1">{{ row.botIndexes.join(', ') }}</v-chip>
              </div>
            </div>
            <v-select
              :model-value="serviceMap[row.gid] ?? ''"
              :items="[
                { title: '未指定', value: '' },
                ...row.botIndexes.map((idx) => ({ title: `Bot #${idx}`, value: String(idx) })),
              ]"
              :disabled="row.botIndexes.length < 2"
              density="compact"
              variant="outlined"
              hide-details
              style="min-width: 140px; max-width: 200px"
              @update:model-value="(v: string) => { serviceMap[row.gid] = v; onServiceChange(row.gid) }"
            />
          </div>
        </div>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn variant="text" @click="emit('update:modelValue', false)">关闭</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.hint {
  display: flex;
  align-items: center;
  gap: 8px;
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-size: 13px;
  padding: 12px 0;
}

.hint.error {
  color: rgb(var(--v-theme-error));
}

.group-list {
  display: flex;
  flex-direction: column;
  max-height: 55vh;
  overflow-y: auto;
}

.group-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 8px;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.06);
}

.group-info {
  min-width: 0;
}

.group-name {
  font-size: 14px;
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.group-meta {
  font-size: 12px;
  color: rgba(var(--v-theme-on-surface), 0.5);
}
</style>
