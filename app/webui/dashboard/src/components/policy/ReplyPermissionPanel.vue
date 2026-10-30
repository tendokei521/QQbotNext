<script setup lang="ts">
/**
 * 「谁能回复」面板：把原来的权限管理字段（响应权限 / 拥有者 / 管理者 / 高危工具审批）
 * 收进「回复与权限」页。
 *
 * 字段分组清单来自 `src/agent/ai-panel.json::reply.groups`（由 scripts/gen_sections.py 生成），
 * 每个字段的 label / description / options 来自后端 `/api/agent/config` 返回的 schema：
 * 这里只负责分组展示与保存，不复制第二套字段定义。
 */
import { computed, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import panelConfig from '@/agent/ai-panel.json'
import { useAgentConfigStore } from '@/stores/agentConfig'
import { useBotsStore } from '@/stores/bots'
import EmptyState from '@/components/EmptyState.vue'
import FieldControl from '@/components/config/FieldControl.vue'
import StringListWidget from '@/components/config/StringListWidget.vue'
import SaveStatusBar from '@/components/common/SaveStatusBar.vue'

interface ReplyGroup {
  id: string
  label: string
  icon: string
  fields: string[]
}

const router = useRouter()
const bots = useBotsStore()
const agent = useAgentConfigStore()

const replyGroups = (panelConfig as { reply?: { groups?: ReplyGroup[] } }).reply?.groups || []

/** 只渲染当前后端 schema 里真的存在的字段：旧版本缺字段时给说明，而不是留一张空卡片 */
const availableGroups = computed(() =>
  replyGroups
    .map((group) => ({
      ...group,
      fields: group.fields.filter((key) => Boolean(schemaOf(key).type)),
    }))
    .filter((group) => group.fields.length > 0),
)

/** 保存状态：与 AI 对话面板使用同一套自动保存与状态条，这里只展示结果 */
const showSaveStatus = computed(() => Boolean(bots.currentAccountId) && agent.loadedFor !== null)

/**
 * 取字段 schema。
 *
 * `/api/agent/config` 下发的 schema 是 `{groups, items}` 分区形态（见 schema_utils.webui_schema），
 * 字段定义在 `items[key]`；这里同时兼容旧版扁平结构，避免后端版本差异导致整块不显示。
 */
function schemaOf(key: string): Record<string, any> {
  const schema = (agent.schema || {}) as Record<string, any>
  const items = schema.items && typeof schema.items === 'object'
    ? (schema.items as Record<string, any>)
    : schema
  return (items[key] || {}) as Record<string, any>
}

function isStringList(key: string): boolean {
  return String(schemaOf(key).type || '').toLowerCase() === 'string_list'
}

/** 名册字段的说明统一收短（后端原文较长，这里按页面口径展示） */
const SHORT_DESCRIPTIONS: Record<string, string> = {
  agent_owner_list: '每行一个 QQ 号，留空即无人拥有',
  agent_manager_list: '每行一个 QQ 号，权限低于拥有者',
  permission: '谁能触发回复：拥有者 > 管理者 > 群主 > 群管 > 成员',
}

function displaySchema(key: string): Record<string, any> {
  const schema = schemaOf(key)
  const short = SHORT_DESCRIPTIONS[key]
  return short ? { ...schema, description: short } : schema
}

function valueOf(key: string): any {
  const value = agent.draft[key]
  return value !== undefined ? value : schemaOf(key).default
}

onMounted(() => {
  void agent.load()
})

// 切账号：强制重载（旧账号的草稿由 store 的 flush 先落盘），避免这里显示上一个账号的权限
watch(
  () => bots.currentAccountId,
  () => {
    void agent.load(true)
  },
)
</script>

<template>
  <div>
    <div class="d-flex align-center flex-wrap gap-2 mb-3">
      <span class="text-caption app-muted">按账号生效：谁能触发、是否需要审批</span>
      <v-spacer />
      <SaveStatusBar v-if="showSaveStatus" :status="agent.saveStatus" autosave />
    </div>

    <v-alert v-if="agent.loadError" type="warning" variant="tonal" density="compact" class="mb-3">
      <div class="d-flex align-center flex-wrap gap-2">
        <span class="text-caption">这些设置读取失败：{{ agent.loadError }}</span>
        <v-btn size="small" variant="text" prepend-icon="mdi-refresh" @click="agent.load(true)">重试</v-btn>
      </div>
    </v-alert>

    <v-card v-if="!bots.currentAccountId" variant="outlined">
      <EmptyState
        icon="mdi-account-key-outline"
        title="还没有账号"
        description="先添加一个 QQ 账号，再决定谁能让它回复。"
      >
        <v-btn color="primary" variant="tonal" prepend-icon="mdi-plus" @click="router.push('/bots')">
          去添加账号
        </v-btn>
      </EmptyState>
    </v-card>

    <template v-else>
      <v-card v-for="group in availableGroups" :key="group.id" variant="outlined" class="mb-4">
        <v-card-title class="d-flex align-center">
          <v-icon :icon="group.icon" class="mr-2" color="primary" />
          {{ group.label }}
        </v-card-title>
        <v-card-text>
          <div v-for="key in group.fields" :key="key" class="reply-field">
            <template v-if="isStringList(key)">
              <div class="reply-field-label">{{ displaySchema(key).label || key }}</div>
              <div v-if="displaySchema(key).description" class="reply-field-desc">
                {{ displaySchema(key).description }}
              </div>
              <StringListWidget
                :model-value="(valueOf(key) as string[]) || []"
                @update:model-value="(v: string[]) => agent.onChange(key, v)"
              />
            </template>
            <FieldControl
              v-else
              :field-key="key"
              :schema="displaySchema(key)"
              :value="valueOf(key)"
              :bot-id="bots.currentAccountId"
              @update="(v: any) => agent.onChange(key, v)"
            />
          </div>
        </v-card-text>
      </v-card>

      <v-card v-if="!availableGroups.length && !agent.loading" variant="outlined">
        <EmptyState
          icon="mdi-form-select"
          title="没有可调整的项"
          description="当前版本没有提供回复权限相关的设置。"
        />
      </v-card>
    </template>
  </div>
</template>

<style scoped>
.reply-field + .reply-field {
  margin-top: 16px;
}

/* 「添加」是普通按钮，不要被 flex 拉伸成一整行输入框的样子 */
.reply-field :deep(.string-list .v-btn) {
  align-self: flex-start;
}

.reply-field-label {
  font-size: 13.5px;
  font-weight: 500;
}

.reply-field-desc {
  font-size: 12px;
  color: rgba(var(--v-theme-on-surface), 0.55);
  line-height: 1.5;
  margin-bottom: 4px;
}
</style>
