<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import http, { errorMessage } from '@/api/http'
import ConfigForm from '@/components/config/ConfigForm.vue'
import AgentSubPage from '@/components/agent/AgentSubPage.vue'
import { useAgentConfigStore } from '@/stores/agentConfig'
import { useBotsStore } from '@/stores/bots'
import { useNotifyStore } from '@/stores/notify'
import { filterSchemaByPage } from '@/utils/schema'

interface KnowledgeItem {
  id: string
  title: string
  content: string
  source: string
  created_at: number
  updated_at: number
}

const agent = useAgentConfigStore()
const bots = useBotsStore()
const notify = useNotifyStore()
const schema = computed(() => filterSchemaByPage(agent.schema, 'knowledge'))

const items = ref<KnowledgeItem[]>([])
const loadingItems = ref(false)
const adding = ref(false)
const addForm = ref({ title: '', content: '' })

async function loadItems() {
  if (!agent.botId) return
  loadingItems.value = true
  try {
    const res = await http.get<{ ok: boolean; items: KnowledgeItem[] }>('/api/agent/knowledge/items', {
      params: { bot_id: agent.botId },
    })
    items.value = res.data.items || []
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    loadingItems.value = false
  }
}

async function addItem() {
  if (!agent.botId) return
  if (!addForm.value.content.trim()) {
    notify.push('内容不能为空', 'warning')
    return
  }
  adding.value = true
  try {
    await http.post('/api/agent/knowledge/items', addForm.value, { params: { bot_id: agent.botId } })
    notify.push('知识条目已添加', 'success')
    addForm.value = { title: '', content: '' }
    await loadItems()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    adding.value = false
  }
}

async function deleteItem(item: KnowledgeItem) {
  if (!agent.botId) return
  try {
    await http.delete(`/api/agent/knowledge/items/${item.id}`, { params: { bot_id: agent.botId } })
    notify.push('知识条目已删除', 'success')
    await loadItems()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

function fmtTime(ts: number): string {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false })
}

onMounted(async () => {
  await agent.load()
  loadItems()
})
watch(
  () => bots.currentBot?.bot_id,
  async () => {
    await agent.load(true)
    loadItems()
  },
)
</script>

<template>
  <AgentSubPage title="知识库" subtitle="知识库检索与 Embedding 模型" icon="mdi-book-open-variant" color="deep-purple">
    <v-card variant="outlined" class="mb-4">
      <v-card-title class="d-flex align-center">
        <v-icon icon="mdi-book-open-variant" class="mr-2" color="deep-purple" /> 知识库配置
        <v-spacer />
        <v-chip v-if="agent.draft.knowledge_enable" color="success" size="small">启用</v-chip>
        <v-chip v-else color="grey" size="small">未启用</v-chip>
      </v-card-title>
      <v-card-text>
        <ConfigForm :module-name="'agent'" :schema="schema" :config="agent.draft" :bot-id="agent.botId" @change="agent.onChange" />
      </v-card-text>
    </v-card>

    <v-card variant="outlined" class="mb-4">
      <v-card-title class="d-flex align-center">
        <v-icon icon="mdi-book-plus-outline" class="mr-2" color="deep-purple" /> 知识条目
        <v-spacer />
        <v-btn size="small" variant="tonal" prepend-icon="mdi-refresh" :loading="loadingItems" @click="loadItems">刷新</v-btn>
      </v-card-title>
      <v-card-text>
        <div class="add-knowledge-row">
          <v-text-field v-model="addForm.title" label="标题（可选）" density="compact" variant="outlined" hide-details />
          <v-text-field v-model="addForm.content" label="内容" density="compact" variant="outlined" hide-details />
          <v-btn color="primary" variant="tonal" prepend-icon="mdi-plus" :loading="adding" @click="addItem">添加</v-btn>
        </div>

        <v-list density="compact" class="mt-3">
          <v-list-item v-for="item in items" :key="item.id">
            <v-list-item-title>{{ item.title || '(无标题)' }}</v-list-item-title>
            <v-list-item-subtitle>{{ item.content }} · {{ fmtTime(item.updated_at) }}</v-list-item-subtitle>
            <template #append>
              <v-btn size="x-small" variant="text" icon="mdi-delete" color="error" @click="deleteItem(item)" />
            </template>
          </v-list-item>
          <v-list-item v-if="!items.length && !loadingItems">
            <v-list-item-title class="text-caption text-center py-3" style="opacity: 0.5">
              暂无知识条目
            </v-list-item-title>
          </v-list-item>
        </v-list>
      </v-card-text>
    </v-card>
  </AgentSubPage>
</template>

<style scoped>
.add-knowledge-row {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}
</style>
