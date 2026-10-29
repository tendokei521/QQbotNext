<script setup lang="ts">
/**
 * 「谁能回复」摘要卡（只读）。
 *
 * 权限档位、拥有者/管理者名册、高危工具审批的唯一编辑入口在「回复与权限」页；
 * 面板只给一眼能看懂的结论 + 跳转，避免同一个字段两处都能改。
 */
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { useAgentConfigStore } from '@/stores/agentConfig'
import { splitSchema } from '@/utils/schema'

/** 后端 schema 的选项表拿不到时的兜底文案（与 config_schema.py 的 permission.options 对齐） */
const FALLBACK_PERMISSION_LABELS: Record<string, string> = {
  everyone: '所有人',
  member: '普通成员及以上',
  group_admin: '群管理或群主',
  group_owner: '仅群主',
  manager: '管理者和拥有者',
  owner: '仅拥有者',
}

const router = useRouter()
const agent = useAgentConfigStore()

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

function goWhoCanReply() {
  router.push({ path: '/reply', query: { tab: 'who' } })
}

function goGroupScope() {
  router.push('/reply')
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
        <span>{{ permissionLabel }}</span>
      </div>
      <div class="scope-row">
        <span class="scope-label">名册</span>
        <span>{{ rosterText }}</span>
      </div>
      <div class="scope-row">
        <span class="scope-label">高危工具审批</span>
        <span>{{ toolApprovalOn ? '开启' : '关闭' }}</span>
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
</style>
