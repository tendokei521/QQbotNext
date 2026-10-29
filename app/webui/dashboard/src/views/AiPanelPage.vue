<script setup lang="ts">
/**
 * AI 对话面板（/ai）：日常操作台。
 *
 * 与「AI 设置」的分工：这里只放**高频、一眼要看到**的东西——
 *   1. 状态条：总开关 / 模型是否就绪 / 当前人格；
 *   2. 回复开关：总开关（群聊/私聊默认在「回复与权限」里改）；
 *   3. 触发方式：**全局触发规则的唯一编辑入口**（生效摘要 + 规则抽屉）；
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
import { useNotifyStore } from '@/stores/notify'
import { errorMessage } from '@/api/http'
import { usePolicyStore, type PolicyDetail } from '@/stores/policy'
import StringListWidget from '@/components/config/StringListWidget.vue'
import { resolvePersona, type PersonaPresetLike } from '@/agent/persona'
import SaveStatusBar from '@/components/common/SaveStatusBar.vue'
import PanelCard from '@/components/agent/panel/PanelCard.vue'
import PanelField from '@/components/agent/panel/PanelField.vue'
import ReplyScopeCard from '@/components/agent/panel/ReplyScopeCard.vue'
import AgentModelPool from '@/components/agent/sections/AgentModelPool.vue'
import AgentRolePresets from '@/components/agent/sections/AgentRolePresets.vue'
import AgentConfigSearchDialog from '@/components/agent/AgentConfigSearchDialog.vue'
import AgentCopyConfigDialog from '@/components/agent/AgentCopyConfigDialog.vue'
import EventMonitorDialog from '@/components/bots/EventMonitorDialog.vue'
import EmptyState from '@/components/EmptyState.vue'

const agent = useAgentConfigStore()
const bots = useBotsStore()
const notify = useNotifyStore()
const router = useRouter()

const searchDialog = ref(false)
const copyDialog = ref(false)
const monitorOpen = ref(false)
/** 首次进入的加载态：避免「账号列表还没回来」时先闪一下无账号空态 */
const booting = ref(true)

const accountId = computed(() => bots.currentAccountId)
const accountName = computed(() => (bots.currentBot ? displayName(bots.currentBot) : ''))
const hasAccount = computed(() => accountId.value !== null)

/** 场景开关（agent 配置）：群聊回复 / 私聊回复 */
const groupDefault = computed<boolean>({
  get: () => Boolean(agent.draft.group_enable),
  set: (v) => agent.onChange('group_enable', v),
})
const privateDefault = computed<boolean>({
  get: () => agent.draft.private_enable !== false,
  set: (v) => agent.onChange('private_enable', v),
})

/** 总开关不是 schema 字段（独立存储），包成同样的 boolean schema 交给 PanelField 统一形态 */
const ENABLED_FIELD_SCHEMA = {
  type: 'boolean',
  label: '启用 AI 回复',
  description: '总开关。关闭后所有场景都不再自动回复。',
}

function onEnabled(value: boolean | null) {
  agent.onEnabledChange(value)
}

/** 全局触发规则：直接展开在「触发方式」卡里（channelId=null = 账号层） */
const policy = usePolicyStore()
const globalDetail = ref<PolicyDetail | null>(null)
const loadingTrigger = ref(false)
const savingTrigger = ref(false)

type TriggerMode = 'at' | 'keyword' | 'both' | 'all' | 'none'
const triggerMode = ref<TriggerMode>('at')
const triggerKeywords = ref<string[]>([])
const triggerProbability = ref('')
/** 已保存的快照：用于判断当前表单是否有未保存改动 */
const savedTrigger = ref('')

const TRIGGER_OPTIONS: { title: string; value: TriggerMode }[] = [
  { title: '仅 @ 我', value: 'at' },
  { title: '仅关键词', value: 'keyword' },
  { title: '@ 或关键词', value: 'both' },
  { title: '所有消息', value: 'all' },
  { title: '都不触发', value: 'none' },
]

function snapshotOf(): string {
  return JSON.stringify({
    mode: triggerMode.value,
    keywords: triggerKeywords.value,
    probability: triggerProbability.value,
  })
}

const triggerDirty = computed(() => snapshotOf() !== savedTrigger.value)

/** 摘要固定来自 /api/policy 的解析结果（账号层覆盖 + 旧键折算后的值） */
async function loadGlobalPolicy() {
  if (!bots.currentAccountId) {
    globalDetail.value = null
    return
  }
  loadingTrigger.value = true
  try {
    globalDetail.value = await policy.getPolicy(null)
    const config = globalDetail.value?.resolved?.config || {}
    const mode = String(config.trigger_mode || 'at')
    triggerMode.value = (['at', 'keyword', 'both', 'all', 'none'].includes(mode)
      ? mode
      : 'at') as TriggerMode
    triggerKeywords.value = Array.isArray(config.trigger_keyword)
      ? config.trigger_keyword.map((item: any) => String(item)).filter(Boolean)
      : []
    triggerProbability.value =
      config.trigger_probability !== undefined ? String(config.trigger_probability) : ''
    savedTrigger.value = snapshotOf()
  } catch {
    globalDetail.value = null
  } finally {
    loadingTrigger.value = false
  }
}

async function saveTrigger() {
  if (!bots.currentAccountId || savingTrigger.value) return
  const config: Record<string, any> = { trigger_mode: triggerMode.value }
  if (triggerMode.value === 'keyword' || triggerMode.value === 'both') {
    config.trigger_keyword = triggerKeywords.value.filter(Boolean)
  }
  if (triggerMode.value === 'all' && triggerProbability.value.trim()) {
    const chance = Number(triggerProbability.value.trim())
    if (!Number.isInteger(chance) || chance < 0 || chance > 100) {
      notify.push('参与概率应为 0-100 的整数', 'error')
      return
    }
    config.trigger_probability = chance
  }
  savingTrigger.value = true
  try {
    await policy.savePolicy(null, { config })
    await loadGlobalPolicy()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    savingTrigger.value = false
  }
}

/** 当前人格卡锚点：状态条结论不跳走，滚动到本页面板内的人格卡 */
const personaSection = ref<HTMLElement | null>(null)

function scrollToPersona() {
  personaSection.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

/**
 * 全局默认模型（模型服务 → 全局设置）。
 *
 * 模型池为空时，后端会改用全局设置的 default_model_id 兜底；不看这个值就催用户
 * 「还没选模型」是误报——用户已经配过默认模型了。
 */
const globalDefaultModelId = ref('')
const globalDefaultModelName = computed(() => {
  const id = globalDefaultModelId.value
  if (!id) return ''
  const hit = agent.providerModels.find((m) => m.id === id)
  return hit ? `${hit.preset_name || hit.preset_id} / ${hit.model}` : '已配置'
})

async function loadGlobalDefaultModel() {
  try {
    const res = await http.get<{ settings?: { default_model_id?: string } }>('/api/provider-settings')
    globalDefaultModelId.value = String(res.data?.settings?.default_model_id || '')
  } catch {
    // 读不到按「没有全局默认」处理：宁可多提示一句，也不假装一切就绪
    globalDefaultModelId.value = ''
  }
}

/**
 * 当前人格：与人格卡共用 `resolvePersona`，避免状态条与卡片各算一套、同屏结论打架。
 *
 * 预设列表用 /api/role-presets（含正文），才能判断"内容与某预设相同但未绑定"。
 */
const rolePresets = ref<PersonaPresetLike[]>([])
const persona = computed(() => resolvePersona(agent.draft, rolePresets.value))

async function loadRolePresets() {
  try {
    const res = await http.get<{ presets?: PersonaPresetLike[] }>('/api/role-presets')
    rolePresets.value = res.data?.presets || []
  } catch {
    // 状态条只是辅助信息：候选拉不到就按"未选择"展示，不打断面板使用
    rolePresets.value = []
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

onMounted(() => {
  void bootstrap()
  void loadRolePresets()
  void loadGlobalDefaultModel()
  void loadGlobalPolicy()
})

// 切账号：整份配置按账号重新拉（有未保存改动时 store 会先落盘）
watch(
  () => bots.currentAccountId,
  (id, prev) => {
    if (id === prev) return
    void agent.load(true)
    void loadGlobalPolicy()
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
        <!-- 保存状态与保存动作统一走共享状态条（自动保存，不再放手动「保存」按钮） -->
        <SaveStatusBar
          v-if="hasAccount && agent.loadedFor !== null"
          :status="agent.saveStatus"
          autosave
          hint="改动会在 2 秒内自动保存"
        />
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

      <!-- 状态条：只读，回答「现在能不能回」；与其他卡片同样式，不额外铺灰底 -->
      <v-card variant="outlined" class="mb-4">
        <v-card-text class="py-2">
          <div class="d-flex align-center flex-wrap gap-3">
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
              <template v-if="globalDefaultModelId">
                <v-chip color="info" variant="tonal" prepend-icon="mdi-api">
                  使用全局默认模型：{{ globalDefaultModelName }}
                </v-chip>
                <v-btn size="small" variant="text" prepend-icon="mdi-arrow-right" @click="router.push('/provider-presets')">
                  去模型服务改默认
                </v-btn>
              </template>
              <template v-else>
                <v-chip color="warning" variant="tonal" prepend-icon="mdi-alert-circle-outline">还没选模型</v-chip>
              </template>
            </template>
            <v-chip color="pink" variant="tonal" prepend-icon="mdi-account-heart">人格：{{ persona.label }}</v-chip>
            <v-btn
              v-if="persona.kind !== 'bound'"
              size="small"
              variant="text"
              prepend-icon="mdi-arrow-down"
              @click="scrollToPersona"
            >
              去下方选择人格
            </v-btn>
          </div>
        </v-card-text>
      </v-card>

      <!-- ① 回复开关：总开关 + 群聊/私聊 -->
      <PanelCard
        title="回复开关"
        icon="mdi-toggle-switch-outline"
        subtitle="只对没有单独设置的群/私聊生效；在「回复与权限」里单独开过的以那边为准"
      >
        <PanelField
          :field-key="'enabled'"
          :schema="ENABLED_FIELD_SCHEMA"
          :value="agent.enabled"
          :bot-id="accountId"
          @update="onEnabled"
        />
        <v-divider class="my-4" />
        <div class="d-flex align-center flex-wrap gap-6">
          <v-switch
            v-model="groupDefault"
            label="群聊回复"
            color="success"
            density="compact"
            hide-details
          />
          <v-switch
            v-model="privateDefault"
            label="私聊回复"
            color="success"
            density="compact"
            hide-details
          />
        </div>
      </PanelCard>

      <!-- ② 触发方式：什么时候接话 -->
      <PanelCard title="触发方式" icon="mdi-at" color="info">
        <v-progress-linear v-if="loadingTrigger" indeterminate color="info" class="mb-3" />
        <v-select
          v-model="triggerMode"
          :items="TRIGGER_OPTIONS"
          label="触发方式"
          density="comfortable"
          variant="outlined"
          hide-details
          class="mb-3"
          :disabled="!hasAccount"
        />
        <div v-if="triggerMode === 'keyword' || triggerMode === 'both'" class="mb-3">
          <div class="text-body-2 mb-2">关键词</div>
          <StringListWidget v-model="triggerKeywords" />
        </div>
        <v-text-field
          v-if="triggerMode === 'all'"
          v-model="triggerProbability"
          label="参与概率（%）"
          type="number"
          min="0"
          max="100"
          density="comfortable"
          variant="outlined"
          hide-details
          class="mb-3"
        />
        <div class="d-flex justify-end">
          <v-btn
            color="primary"
            variant="tonal"
            :loading="savingTrigger"
            :disabled="!hasAccount || !triggerDirty"
            @click="saveTrigger"
          >
            保存
          </v-btn>
        </div>
      </PanelCard>

      <!-- ③ 模型：复用设置区的模型卡（自读 store，不需要 props）；加入动作只在卡里出现一次 -->
      <div class="panel-block">
        <AgentModelPool />
      </div>

      <!-- ④ 人格：复用设置区的人格卡（唯一「应用人格」入口） -->
      <div ref="personaSection" class="panel-block">
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
    <AgentConfigSearchDialog v-model="searchDialog" />
    <AgentCopyConfigDialog v-model="copyDialog" />
  </div>
</template>

<style scoped>
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
</style>
