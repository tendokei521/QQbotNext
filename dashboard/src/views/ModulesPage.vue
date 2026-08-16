<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useModulesStore, type ModuleData } from '@/stores/modules'
import { useNotifyStore } from '@/stores/notify'
import { errorMessage } from '@/api/http'
import EmptyState from '@/components/EmptyState.vue'

const modules = useModulesStore()
const notify = useNotifyStore()
const router = useRouter()

const search = ref('')
const collapsed = ref<Record<string, boolean>>({})
const searchRef = ref<HTMLInputElement | null>(null)

const PERMISSION_LABEL: Record<string, string> = {
  everyone: '所有人',
  member: '成员',
  group_admin: '管理员',
  group_owner: '群主',
  owner: '所有者',
}

const PERMISSION_COLOR: Record<string, string> = {
  everyone: 'success',
  member: 'primary',
  group_admin: 'info',
  group_owner: 'purple',
  owner: 'error',
}

const filtered = computed(() => {
  const kw = search.value.trim().toLowerCase()
  if (!kw) return modules.list
  return modules.list.filter((m) => {
    const name = (m.name + ' ' + m.name_sign + ' ' + (m.tags || []).join(' ')).toLowerCase()
    return name.includes(kw)
  })
})

const groups = computed(() => {
  const list = filtered.value
  // 固定模块置顶，其余按 category 分组
  const pinned = list.filter((m) => m.pinned)
  const byCategory = new Map<string, ModuleData[]>()
  list
    .filter((m) => !m.pinned)
    .forEach((m) => {
      const cat = m.category || '未分类'
      if (!byCategory.has(cat)) byCategory.set(cat, [])
      byCategory.get(cat)!.push(m)
    })
  const cats = [...byCategory.entries()].sort((a, b) => a[0].localeCompare(b[0], 'zh-CN'))
  return { pinned, cats }
})

function isCollapsed(cat: string): boolean {
  return !!collapsed.value[cat]
}

function toggleCat(cat: string) {
  collapsed.value[cat] = !collapsed.value[cat]
  localStorage.setItem('qqbot_module_collapsed', JSON.stringify(collapsed.value))
}

function onSearch() {
  // 搜索时自动展开所有分组（无搜索词时恢复折叠态）
}

async function onToggle(m: ModuleData, v: boolean | null) {
  const enabled = !!v
  try {
    await modules.toggle(m.name, enabled)
    notify.push(`模块 ${m.name} 已${enabled ? '启用' : '禁用'}`, 'success')
  } catch (err) {
    notify.push(errorMessage(err), 'error')
    m.enabled = !enabled // 回滚
  }
}

function onKeydown(e: KeyboardEvent) {
  const tag = (e.target as HTMLElement)?.tagName
  if (e.key === '/' && tag !== 'INPUT' && tag !== 'TEXTAREA') {
    e.preventDefault()
    searchRef.value?.focus()
  }
}

onMounted(() => {
  try {
    collapsed.value = JSON.parse(localStorage.getItem('qqbot_module_collapsed') || '{}')
  } catch {
    collapsed.value = {}
  }
  if (!modules.list.length) modules.load()
  window.addEventListener('keydown', onKeydown)
})

onUnmounted(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <div>
    <div class="app-page-header">
      <div>
        <h1 class="app-page-title">功能模块</h1>
        <div class="app-page-subtitle">共 {{ modules.count }} 个模块，点击进入配置</div>
      </div>
      <v-btn variant="tonal" prepend-icon="mdi-refresh" :loading="modules.reloading" @click="modules.reloadAll()">
        刷新模块
      </v-btn>
    </div>

    <v-text-field
      ref="searchRef"
      v-model="search"
      placeholder="搜索模块…（按 / 快速聚焦）"
      density="comfortable"
      variant="outlined"
      hide-details
      clearable
      prepend-inner-icon="mdi-magnify"
      class="mb-4"
      @update:model-value="onSearch"
    />

    <!-- 首载骨架屏 -->
    <v-row v-if="modules.loading && modules.list.length === 0">
      <v-col v-for="i in 6" :key="i" cols="12" md="6" xl="4">
        <v-skeleton-loader type="card-avatar, article" class="rounded-xl" />
      </v-col>
    </v-row>

    <template v-else>
      <!-- 固定模块 -->
      <div v-if="groups.pinned.length" class="mb-4">
        <div class="group-heading">置顶</div>
        <v-row>
          <v-col v-for="m in groups.pinned" :key="m._key" cols="12" md="6" xl="4">
            <v-card variant="outlined" class="module-card app-card-hover" @click="router.push(`/modules/${m._key}`)">
              <v-card-item>
                <template #prepend>
                  <v-avatar color="lightprimary" size="36" rounded="lg">
                    <v-icon icon="mdi-pin" color="primary" size="small" />
                  </v-avatar>
                </template>
                <v-card-title class="text-body-1">
                  <span class="app-line-clamp-1">{{ m.name }}</span>
                  <v-chip size="x-small" class="ml-1" :color="PERMISSION_COLOR[m.permission] || 'default'" variant="tonal">
                    {{ PERMISSION_LABEL[m.permission] || m.permission }}
                  </v-chip>
                </v-card-title>
                <v-card-subtitle class="app-line-clamp-2">{{ m.description }}</v-card-subtitle>
                <template #append>
                  <v-switch
                    :model-value="m.enabled"
                    color="primary"
                    density="compact"
                    hide-details
                    @click.stop
                    @update:model-value="(v: boolean | null) => onToggle(m, v)"
                  />
                </template>
              </v-card-item>
            </v-card>
          </v-col>
        </v-row>
      </div>

      <!-- 分类分组 -->
      <div v-for="[cat, items] in groups.cats" :key="cat" class="mb-3">
        <div class="group-heading d-flex align-center" @click="toggleCat(cat)">
          <v-icon :icon="isCollapsed(cat) ? 'mdi-chevron-right' : 'mdi-chevron-down'" size="small" class="mr-1" />
          {{ cat }}
          <v-chip size="x-small" variant="flat" class="ml-2">{{ items.length }}</v-chip>
        </div>
        <v-row v-show="!isCollapsed(cat)">
          <v-col v-for="m in items" :key="m._key" cols="12" md="6" xl="4">
            <v-card variant="outlined" class="module-card app-card-hover" @click="router.push(`/modules/${m._key}`)">
              <v-card-item>
                <template #prepend>
                  <v-avatar color="lightprimary" size="36" rounded="lg">
                    <v-icon icon="mdi-cube-outline" color="primary" size="small" />
                  </v-avatar>
                </template>
                <v-card-title class="text-body-1">
                  <span class="app-line-clamp-1">{{ m.name }}</span>
                  <v-chip size="x-small" class="ml-1" :color="PERMISSION_COLOR[m.permission] || 'default'" variant="tonal">
                    {{ PERMISSION_LABEL[m.permission] || m.permission }}
                  </v-chip>
                </v-card-title>
                <v-card-subtitle class="app-line-clamp-2">{{ m.description }}</v-card-subtitle>
                <template #append>
                  <v-switch
                    :model-value="m.enabled"
                    color="primary"
                    density="compact"
                    hide-details
                    @click.stop
                    @update:model-value="(v: boolean | null) => onToggle(m, v)"
                  />
                </template>
              </v-card-item>
            </v-card>
          </v-col>
        </v-row>
      </div>

      <v-card v-if="filtered.length === 0 && !modules.loading" variant="outlined">
        <EmptyState
          icon="mdi-magnify-close"
          :title="search ? `没有匹配「${search}」的模块` : '暂无模块'"
          description="尝试更换关键词，或刷新模块列表"
        >
          <v-btn size="small" variant="tonal" prepend-icon="mdi-refresh" @click="modules.reloadAll()">刷新模块</v-btn>
        </EmptyState>
      </v-card>
    </template>
  </div>
</template>

<style scoped>
.group-heading {
  font-size: 14px;
  font-weight: 600;
  color: rgba(var(--v-theme-on-surface), 0.7);
  padding: 8px 2px;
  cursor: pointer;
  user-select: none;
}

.module-card {
  height: 100%;
  border-radius: 14px;
  cursor: pointer;
}

.module-card :deep(.v-card-item__title) {
  display: flex;
  align-items: center;
  min-width: 0;
  white-space: normal;
}

.module-card :deep(.v-card-item__subtitle) {
  min-height: 36px;
  line-height: 1.5;
}
</style>
