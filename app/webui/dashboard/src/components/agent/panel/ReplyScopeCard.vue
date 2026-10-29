<script setup lang="ts">
/**
 * 「谁能回复」摘要卡（只读）。
 *
 * 权限档位、名册、高危工具审批的唯一编辑入口在「回复与权限 → 谁能回复」；
 * 用户例外（黑/白名单）在「高级规则」、逐群例外在「群与私聊」。面板只给一眼能看懂的
 * 结论 + 跳转，避免同一个字段两处都能改，也避免摘要漏掉真正生效的例外。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useAgentConfigStore } from '@/stores/agentConfig'
import { usePolicyStore } from '@/stores/policy'
import { splitSchema } from '@/utils/schema'

/** 后端 schema 的选项表拿不到时的替代文案（与 config_schema.py 的 permission.options 对齐） */
const FALLBACK_PERMISSION_LABELS: Record<string, string> = {
  everyone: '所有人',
  member: '群成员及以上',
  group_admin: '群管及以上',
  group_owner: '仅群主',
  manager: '管理者与拥有者',
  owner: '仅拥有者',
}

const router = useRouter()
const agent = useAgentConfigStore()
const policy = usePolicyStore()

/** 账号层「用户例外」：null = 还没取到（失败/加载中），UI 显示跳转入口 */
const userException = ref<{ mode: string; count: number } | null>(null)
/** 有单独覆盖的群数量：null = 还没取到 */
const overrideGroupCount = ref<number | null>(null)

/** 档位显示名：优先用后端下发的 options，避免前后端文案各写一份 */
const permissionLabel = computed(() => {
  const key = String(agent.draft.permission || '')
  const def = splitSchema(agent.schema).items.permission
  const options = def?.options
  if (options && typeof options === 'object') {
    if (Array.isArray(options)) {
      const hit = options.find((item: any) => String(item?.value ?? item) === key)
      if (hit) return String(hit.label ?? hit)
    } else if (options[key] !== undefined) {
      return String(options[key])
    }
  }
  return FALLBACK_PERMISSION_LABELS[key] || '未设置'
})

/** 名册可能是数组，也可能是多行文本（历史数据/直接改库）——统一按有效条目计数 */
function countList(value: unknown): number {
  if (Array.isArray(value)) return value.filter((item) => String(item ?? '').trim() !== '').length
  if (typeof value === 'string') return value.split(/[\n,，]/).map((item) => item.trim()).filter(Boolean).length
  return 0
}

const ownerCount = computed(() => countList(agent.draft.agent_owner_list))
const managerCount = computed(() => countList(agent.draft.agent_manager_list))

const rosterText = computed(() => {
  if (ownerCount.value + managerCount.value === 0) return '还没有名册'
  return `拥有者 ${ownerCount.value} 人 · 管理者 ${managerCount.value} 人`
})

const toolApprovalOn = computed(() => Boolean(agent.draft.tool_approval_enable))

const userExceptionText = computed(() => {
  const info = userException.value
  if (info === null) return '去「回复与权限」查看'
  if (info.mode === 'blacklist') return `禁止 ${info.count} 人`
  if (info.mode === 'whitelist') return `允许 ${info.count} 人`
  return '无'
})

const overrideGroupText = computed(() => {
  if (overrideGroupCount.value === null) return '去「回复与权限」查看'
  return overrideGroupCount.value === 0 ? '无' : `${overrideGroupCount.value} 个`
})

/**
 * 账号级用户例外 + 群级覆盖数量。
 *
 * 两个请求互相独立：任一失败只让对应一行退化成“去回复与权限查看”，不影响摘要其它内容。
 */
async function loadPolicyInfo() {
  try {
    const detail = await policy.getPolicy(null)
    const permission = (detail?.overrides?.account?.permission || {}) as Record<string, any>
    const mode = String(permission.user_mode || '')
    const list = Array.isArray(permission.user_list) ? permission.user_list : []
    userException.value = { mode, count: list.length }
  } catch {
    userException.value = null
  }
  try {
    if (!policy.rows.length) await policy.load()
    overrideGroupCount.value = policy.rows.filter((row) => (row.overridden_keys || []).length > 0).length
  } catch {
    overrideGroupCount.value = null
  }
}

onMounted(() => {
  void loadPolicyInfo()
})

watch(
  () => policy.accountId,
  () => {
    void loadPolicyInfo()
  },
)

function goWhoCanReply() {
  router.push({ path: '/reply', query: { tab: 'who' } })
}

function goGroupScope() {
  router.push('/reply')
}

function goExceptionRules() {
  router.push({ path: '/reply', query: { tab: 'rules' } })
}
</script>

<template>
  <v-card variant="outlined" class="mb-4">
    <v-card-title class="d-flex align-center">
      <v-icon icon="mdi-shield-account-outline" color="success" class="mr-2" />
      谁能回复
      <v-spacer />
      <v-btn size="small" variant="tonal" prepend-icon="mdi-tune" @click="goWhoCanReply">去调整</v-btn>
    </v-card-title>
    <v-card-text class="pt-0">
      <div class="scope-row">
        <span class="scope-label">当前档位</span>
        <span class="scope-value">{{ permissionLabel }}</span>
      </div>
      <div class="scope-row">
        <span class="scope-label">名册</span>
        <span class="scope-value">{{ rosterText }}</span>
      </div>
      <div class="scope-row">
        <span class="scope-label">用户例外</span>
        <span class="scope-value">{{ userExceptionText }}</span>
        <v-btn size="x-small" variant="text" append-icon="mdi-chevron-right" @click="goExceptionRules">调整</v-btn>
      </div>
      <div class="scope-row">
        <span class="scope-label">单独设置的群</span>
        <span class="scope-value">{{ overrideGroupText }}</span>
        <v-btn size="x-small" variant="text" append-icon="mdi-chevron-right" @click="goGroupScope">查看</v-btn>
      </div>
      <div class="scope-row">
        <span class="scope-label">高危工具审批</span>
        <span class="scope-value">{{ toolApprovalOn ? '开启' : '关闭' }}</span>
      </div>
      <v-divider class="my-3" />
      <div class="d-flex align-center flex-wrap gap-2">
        <v-btn size="small" variant="text" prepend-icon="mdi-account-group-outline" @click="goGroupScope">
          逐群回复范围
        </v-btn>
        <span class="text-caption" style="opacity: 0.6">这里只显示摘要，修改在「回复与权限」</span>
      </div>
    </v-card-text>
  </v-card>
</template>

<style scoped>
.scope-row {
  display: flex;
  align-items: baseline;
  gap: 12px;
  padding: 4px 0;
  font-size: 13.5px;
}

.scope-label {
  width: 96px;
  flex: 0 0 auto;
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-size: 12.5px;
}

.scope-value {
  flex: 1 1 auto;
  min-width: 0;
}
</style>
