<script setup lang="ts">
/**
 * AI 对话面板（/ai）：日常操作台。
 *
 * 与「AI 设置」的分工：这里只放**高频、一眼要看到**的东西——
 *   1. 状态条：总开关 / 模型是否就绪 / 当前人格；
 *   2. 回复开关：agent.enabled + 群聊、私聊开关；
 *   3. 触发方式：被 @ / 关键词；
 *   4. 模型池与人格（直接复用设置区已有的定制组件，不写第二套表单）；
 *   5. 谁能回复：只读摘要 + 跳转「回复与权限」。
 *
 * 字段清单的唯一真相是 ``@/agent/ai-panel.json``（后端从 config_partition 生成），
 * 这里不硬编码字段名；保存沿用 agentConfig store 的 2 秒自动保存 + 保存状态。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import http from '@/api/http'
import { useAgentConfigStore } from '@/stores/agentConfig'
import { displayName, useBotsStore } from '@/stores/bots'
import { splitSchema } from '@/utils/schema'
import panelManifest from '@/agent/ai-panel.json'
import PanelCard from '@/components/agent/panel/PanelCard.vue'
import PanelField from '@/components/agent/panel/PanelField.vue'
import ReplyScopeCard from '@/components/agent/panel/ReplyScopeCard.vue'
import AgentModelPool from '@/components/agent/sections/AgentModelPool.vue'
import AgentRolePresets from '@/components/agent/sections/AgentRolePresets.vue'
import AgentTemplateDialog from '@/components/agent/AgentTemplateDialog.vue'
import AgentConfigSearchDialog from '@/components/agent/AgentConfigSearchDialog.vue'
import AgentCopyConfigDialog from '@/components/agent/AgentCopyConfigDialog.vue'
import EventMonitorDialog from '@/components/bots/EventMonitorDialog.vue'
import EmptyState from '@/components/EmptyState.vue'

interface PanelCardDef {
  id: string
  label: string
  icon: string
  fields: string[]
}

/** 面板字段卡清单（后端生成，勿手改 JSON） */
const PANEL_CARDS: PanelCardDef[] =
  (panelManifest as unknown as { panel?: { cards?: PanelCardDef[] } }).panel?.cards || []
const replyCard = PANEL_CARDS.find((card) => card.id === 'panel_reply')
const triggerCard = PANEL_CARDS.find((card) => card.id === 'panel_trigger')

const agent = useAgentConfigStore()
const bots = useBotsStore()
const router = useRouter()

const templateDialog = ref(false)
const searchDialog = ref(false)
const copyDialog = ref(false)
const monitorOpen = ref(false)
/** 首次进入的加载态：避免「账号列表还没回来」时先闪一下无账号空态 */
const booting = ref(true)

const accountId = computed(() => bots.currentAccountId)
const accountName = computed(() => (bots.currentBot ? displayName(bots.currentBot) : ''))
const hasAccount = computed(() => accountId.value !== null)

const schemaItems = computed<Record<string, any>>(() => splitSchema(agent.schema).items)

/** 只渲染 schema 里真实存在的字段：字段清单可能比后端版本新，缺了就跳过而不是渲染裸 key */
function fieldsOf(card: PanelCardDef | undefined): string[] {
  return (card?.fields || []).filter((key) => Boolean(schemaItems.value[key]?.type))
}
const replyFields = computed(() => fieldsOf(replyCard))
const triggerFields = computed(() => fieldsOf(triggerCard))

function fieldSchema(key: string): Record<string, any> {
  return schemaItems.value[key] || {}
}

function fieldValue(key: string): any {
  const value = agent.draft[key]
  return value !== undefined ? value : fieldSchema(key).default
}

function onField(key: string, value: any) {
  agent.onChange(key, value)
}

function onEnabled(value: boolean | null) {
  agent.onEnabledChange(value)
}

/** 关键词可能是数组，也可能是历史数据里的多行文本 */
const keywordList = computed<string[]>(() => {
  const value = agent.draft.trigger_keyword
  if (Array.isArray(value)) return value.map((item) => String(item ?? '').trim()).filter(Boolean)
  if (typeof value === 'string') return value.split(/[\n,，]/).map((item) => item.trim()).filter(Boolean)
  return []
})

/** 触发方式的一句话结论：让用户不用看懂两个开关的组合效果 */
const triggerEffect = computed(() => {
  const at = Boolean(agent.draft.trigger_at)
  const keyword = keywordList.value.length > 0
  if (at && keyword) return '被 @ 或命中关键词时回复'
  if (at) return '被 @ 时回复'
  if (keyword) return '消息命中关键词时回复'
  return '没有触发方式，群里不会自动接话'
})

/** 保存状态 → 顶部 chip（文案与 AgentLayout 保持一致） */
const saveChip = computed(() => {
  switch (agent.saveStatus) {
    case 'saving':
      return { text: '保存中…', color: 'primary', icon: 'mdi-progress-clock' }
    case 'dirty':
      return { text: '有未保存修改', color: 'warning', icon: 'mdi-pencil-outline' }
    case 'error':
      return { text: '保存失败', color: 'error', icon: 'mdi-alert-circle-outline' }
    case 'blocked':
      return { text: '未选账号，改动不会保存', color: 'grey', icon: 'mdi-account-off-outline' }
    default:
      return { text: '已保存', color: 'success', icon: 'mdi-check-circle-outline' }
  }
})

/** 当前人格名：从资源目录（role_preset 候选）里按 id 找名字，拿不到就不展示内部 id */
const rolePresetOptions = ref<{ value: string; label: string }[]>([])
const personaName = computed(() => {
  const id = String(agent.draft.persona_preset_id || '')
  if (!id) return ''
  return rolePresetOptions.value.find((item) => item.value === id)?.label || ''
})
const personaText = computed(() => {
  if (!String(agent.draft.persona_preset_id || '')) return '未选择'
  return personaName.value || '该人格已删除'
})

async function loadRolePresets() {
  try {
    const res = await http.get<{ options?: { value: string; label: string }[] }>('/api/resources/role_preset')
    rolePresetOptions.value = res.data?.options || []
  } catch {
    // 状态条只是辅助信息：候选拉不到就显示「未选择」，不打断面板使用
  }
}

async function bootstrap() {
  try {
    await agent.load()
  } finally {
    booting.value = false
  }
}

function reload() {
  void agent.load(true)
}

async function saveNow() {
  await agent.save()
}

onMounted(() => {
  void bootstrap()
  void loadRolePresets()
})

// 切账号：整份配置按账号重新拉（有未保存改动时 store 会先落盘）
watch(
  () => bots.currentAccountId,
  (id, prev) => {
    if (id === prev) return
    void agent.load(true)
  },
)
</script>

<template>
  <div>
    <div class="app-page-header" style="align-items: center">
      <div>
        <h1 class="app-page-title">
          <v-icon icon="mdi-chat-processing-outline" size="28" color="primary" class="mr-1" />
          AI 对话面板
        </h1>
        <div class="app-page-subtitle">日常开关、触发方式与状态</div>
      </div>
      <div class="d-flex align-center flex-wrap gap-2">
        <v-chip v-if="accountName" size="small" variant="tonal" prepend-icon="mdi-account">
          当前账号：{{ accountName }}
        </v-chip>
        <!-- 配置还没成功加载时不要显示「已保存」：那是没读到内容，不是已经存好 -->
        <v-chip v-if="hasAccount && agent.loadedFor !== null" size="small" :color="saveChip.color" variant="tonal">
          <v-icon start size="x-small" :icon="saveChip.icon" />
          {{ saveChip.text }}
        </v-chip>
        <v-btn size="small" variant="text" prepend-icon="mdi-book-multiple" @click="templateDialog = true">
          配置模板
        </v-btn>
        <v-btn size="small" variant="text" prepend-icon="mdi-magnify" @click="searchDialog = true">
          搜索配置
        </v-btn>
        <v-btn size="small" variant="text" prepend-icon="mdi-content-copy" @click="copyDialog = true">
          复制配置
        </v-btn>
        <v-btn
          size="small"
          variant="tonal"
          prepend-icon="mdi-stethoscope"
          :disabled="!hasAccount"
          @click="monitorOpen = true"
        >
          诊断
        </v-btn>
        <v-btn
          color="primary"
          variant="tonal"
          prepend-icon="mdi-content-save"
          :loading="agent.saveStatus === 'saving'"
          :disabled="!hasAccount"
          @click="saveNow"
        >
          保存
        </v-btn>
      </div>
    </div>

    <v-progress-linear v-if="booting || agent.loading" indeterminate color="primary" class="mb-3" />

    <!-- 一个账号都没有：先给唯一的下一步，而不是白屏 -->
    <v-card v-if="!hasAccount && !booting" variant="outlined">
      <EmptyState icon="mdi-robot-off-outline" title="还没有账号" description="先添加一个 QQ 账号，再回来设置回复">
        <v-btn color="primary" variant="tonal" prepend-icon="mdi-plus" @click="router.push('/bots')">
          去添加账号
        </v-btn>
      </EmptyState>
    </v-card>

    <v-skeleton-loader v-else-if="booting" type="card" class="rounded-xl" />

    <template v-else>
      <v-alert v-if="agent.loadError" type="warning" variant="tonal" density="compact" class="mb-3">
        <div class="d-flex align-center flex-wrap gap-2">
          <span class="text-caption">配置加载失败：{{ agent.loadError }}</span>
          <v-btn size="small" variant="text" prepend-icon="mdi-refresh" @click="reload">重试</v-btn>
        </div>
      </v-alert>

      <!-- 状态条：只读，回答「现在能不能回」 -->
      <v-card variant="tonal" class="mb-4">
        <v-card-text class="d-flex align-center flex-wrap gap-3 py-2">
          <v-chip
            :color="agent.enabled ? 'success' : 'grey'"
            variant="tonal"
            :prepend-icon="agent.enabled ? 'mdi-check-circle-outline' : 'mdi-pause-circle-outline'"
          >
            {{ agent.enabled ? 'AI 回复已开启' : 'AI 回复已关闭' }}
          </v-chip>
          <template v-if="agent.poolModelIds.length">
            <v-chip color="info" variant="tonal" prepend-icon="mdi-api">
              模型 {{ agent.poolModelIds.length }} 个
            </v-chip>
          </template>
          <template v-else>
            <v-chip color="warning" variant="tonal" prepend-icon="mdi-alert-circle-outline">还没选模型</v-chip>
            <v-btn size="small" variant="text" prepend-icon="mdi-arrow-right" @click="router.push('/provider-presets')">
              去「模型服务」
            </v-btn>
          </template>
          <v-chip color="pink" variant="tonal" prepend-icon="mdi-account-heart">人格：{{ personaText }}</v-chip>
          <v-btn v-if="!personaName" size="small" variant="text" prepend-icon="mdi-arrow-right" @click="router.push('/personas')">
            去「人格设定」
          </v-btn>
          <v-spacer />
          <span class="text-caption" style="opacity: 0.6">改动会在 2 秒内自动保存</span>
        </v-card-text>
      </v-card>

      <!-- ① 回复开关：总开关 + 群聊/私聊 -->
      <PanelCard
        title="回复开关"
        icon="mdi-toggle-switch-outline"
        subtitle="群聊与私聊分开控制；关掉后对应场景不再自动回复"
      >
        <div class="switch-row">
          <div>
            <div class="switch-title">启用 AI 回复</div>
            <div class="switch-desc">总开关。关闭后所有场景都不再自动回复。</div>
          </div>
          <v-switch
            :model-value="agent.enabled"
            color="primary"
            inset
            hide-details
            @update:model-value="onEnabled"
          />
        </div>
        <v-divider class="my-4" />
        <div class="field-stack">
          <PanelField
            v-for="key in replyFields"
            :key="key"
            :field-key="key"
            :schema="fieldSchema(key)"
            :value="fieldValue(key)"
            :bot-id="accountId"
            @update="(v: any) => onField(key, v)"
          />
        </div>
      </PanelCard>

      <!-- ② 触发方式：什么时候接话 -->
      <PanelCard title="触发方式" icon="mdi-at" color="info" subtitle="群聊里什么时候该接话">
        <div class="field-stack">
          <PanelField
            v-for="key in triggerFields"
            :key="key"
            :field-key="key"
            :schema="fieldSchema(key)"
            :value="fieldValue(key)"
            :bot-id="accountId"
            @update="(v: any) => onField(key, v)"
          />
        </div>
        <div class="effect-line mt-4">
          <v-icon icon="mdi-lightbulb-on-outline" size="small" color="info" class="mr-2" />
          当前：{{ triggerEffect }}
        </div>
      </PanelCard>

      <!-- ③ 模型：复用设置区的模型池组件（自读 store，不需要 props） -->
      <div class="panel-block">
        <div class="panel-block-title">
          <v-icon icon="mdi-api" size="small" color="info" class="mr-2" />
          模型
        </div>
        <AgentModelPool />
      </div>

      <!-- ④ 人格：复用设置区的「提示词方案 + 人格预设」组件 -->
      <div class="panel-block">
        <div class="panel-block-title">
          <v-icon icon="mdi-account-voice" size="small" color="pink" class="mr-2" />
          人格
        </div>
        <AgentRolePresets />
      </div>

      <!-- ⑤ 谁能回复：只读摘要 + 跳「回复与权限」 -->
      <ReplyScopeCard />

      <div class="d-flex justify-end">
        <v-btn variant="text" append-icon="mdi-arrow-right" @click="router.push('/ai/settings/behavior')">
          更多设置
        </v-btn>
      </div>
    </template>

    <EventMonitorDialog v-model="monitorOpen" :account-id="accountId" :account-name="accountName" />
    <AgentTemplateDialog v-model="templateDialog" />
    <AgentConfigSearchDialog v-model="searchDialog" />
    <AgentCopyConfigDialog v-model="copyDialog" />
  </div>
</template>

<style scoped>
.switch-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.switch-title {
  font-size: 14px;
  font-weight: 600;
}

.switch-desc {
  font-size: 12.5px;
  color: rgba(var(--v-theme-on-surface), 0.55);
  margin-top: 2px;
}

.field-stack {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.effect-line {
  display: flex;
  align-items: center;
  font-size: 13px;
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.panel-block {
  margin-bottom: 8px;
}

.panel-block-title {
  display: flex;
  align-items: center;
  font-size: 14px;
  font-weight: 700;
  margin: 0 0 8px 2px;
}
</style>
