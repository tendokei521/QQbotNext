<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import http, { errorMessage } from '@/api/http'
import { useWebuiStore } from '@/stores/webui'
import { useNotifyStore } from '@/stores/notify'

interface GroupRow {
  gid: string
  name: string
  /** 该群里有几个账号在线：存账号 id（不是下标） */
  botIndexes: string[]
}

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void }>()

const webui = useWebuiStore()
const notify = useNotifyStore()
const router = useRouter()

const rows = ref<GroupRow[]>([])
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
    const res = await http.get<{ bots_groups: Record<string, { account_id: string; name: string; bot_id: number | null; groups: number[]; groups_info: { group_id?: number; group_name?: string }[] }> }>('/api/bots/groups')
    const botsGroups = res.data?.bots_groups || {}

    // 汇总所有群：gid -> 出现过的 bot index 列表 + 群名
    const map = new Map<string, GroupRow>()
    Object.values(botsGroups).forEach((bg) => {
      const infos = bg.groups_info || []
      bg.groups.forEach((gid, i) => {
        const key = String(gid)
        const row = map.get(key) || { gid: key, name: '', botIndexes: [] }
        // 优先填真实群名；后到的 bot 也可补填，避免某个 bot 无 group_name 时永远显示裸 gid
        if (infos[i]?.group_name) row.name = String(infos[i].group_name)
        if (!row.name) row.name = key
        row.botIndexes.push(bg.account_id)
        map.set(key, row)
      })
    })
    rows.value = [...map.values()]
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    loading.value = false
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

/** 群级回复规则不在这里改：直接带去「回复与权限 → 群与私聊」 */
function openGroupRules() {
  emit('update:modelValue', false)
  router.push({ path: '/reply', query: { tab: 'groups' } })
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
        <v-icon icon="mdi-layers-triple-outline" class="mr-2" color="primary" /> 群与账号概览
      </v-card-title>
      <v-card-text>
        <div class="d-flex align-center flex-wrap gap-2 mb-3">
          <span class="text-caption" style="opacity: 0.7">
            这里只汇总「群」和「哪些账号在群里」，群级的回复规则在「回复与权限 → 群与私聊」里按群调整。
          </span>
          <v-btn size="small" variant="text" color="primary" prepend-icon="mdi-shield-account-outline" @click="openGroupRules">
            去设置群规则
          </v-btn>
        </div>
        <v-checkbox :model-value="showAll" label="展示全部（包括仅单个账号在的群）" density="compact" hide-details color="primary" class="mb-2" @update:model-value="onToggleShowAll" />

        <div v-if="loading" class="hint"><v-progress-circular size="18" indeterminate /> 加载中…</div>
        <div v-else-if="error" class="hint error">
          <span>{{ error }}</span>
          <v-btn size="x-small" variant="text" @click="load">重试</v-btn>
        </div>
        <div v-else class="group-list">
          <div v-if="visibleRows.length === 0" class="hint">还没有群数据：等账号连上、在群里收到消息后这里会自动出现。</div>
          <div v-for="row in visibleRows" :key="row.gid" class="group-row">
            <div class="group-info">
              <div class="group-name">{{ row.name }}</div>
              <div class="group-meta">
                {{ row.gid }} · {{ row.botIndexes.length }} 个账号
              </div>
            </div>
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
  font-size: 12px;
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
