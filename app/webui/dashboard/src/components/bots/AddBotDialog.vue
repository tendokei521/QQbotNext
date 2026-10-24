<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useBotsStore, type BotConnMode } from '@/stores/bots'
import { useNotifyStore } from '@/stores/notify'
import { errorMessage } from '@/api/http'

/**
 * 新增账号：先选连接方式，再创建。
 *
 * 文案一律用大白话：用户脑子里想的是「让我的机器人接进来」还是「我去连它」，
 * 不是「正向/反向 WebSocket」「OneBot 实现」这些词。产品名（NapCat / Lagrange）
 * 保留，因为那正是用户手上那个东西的名字。
 *
 * 为什么不直接插一张空卡片：空卡片的地址是空的，用户点「连接」才看到「连接失败」，
 * 而且等待对方连进来的账号还需要用户自己知道要填端口和 `/onebot/v11/ws` 路径。
 * 先把两条路摆出来选一次，创建时后端按模式给好默认地址，新卡片一出生就能用。
 */
const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'created', accountId: string): void
}>()

const bots = useBotsStore()
const notify = useNotifyStore()

interface ModeOption {
  value: BotConnMode
  icon: string
  title: string
  desc: string
  tips: string[]
  recommended?: boolean
}

const OPTIONS: ModeOption[] = [
  {
    value: 'reverse',
    icon: 'mdi-lan-connect',
    title: '让机器人程序连过来',
    desc: '本程序开一个入口，等 QQ 机器人程序连进来。',
    tips: ['NapCat / Lagrange 等程序都支持', '两个程序装在同一台电脑上时最省事'],
    recommended: true,
  },
  {
    value: 'forward',
    icon: 'mdi-lan',
    title: '本程序主动连过去',
    desc: '你提供 QQ 机器人程序的地址，本程序自己连上去。',
    tips: ['需要先在对方程序里打开 WebSocket 服务', '地址一般长这样：ws://127.0.0.1:3001/'],
  },
]

const selected = ref<BotConnMode>('reverse')
const creating = ref(false)
/** 展示名（随便改） */
const name = ref('')
/** 账号 id（唯一凭据，创建后不可改）：只允许字母/数字/下划线/连字符 */
const accountId = ref('')

const ACCOUNT_ID_RE = /^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$/
const nameError = computed(() => '')
const idError = computed(() => {
  const v = accountId.value.trim()
  if (!v) return '请填写账号 ID'
  if (!ACCOUNT_ID_RE.test(v)) return '只能用字母、数字、下划线、连字符，最多 32 个字符'
  if (['none', 'null', 'global', '_global'].includes(v.toLowerCase())) return '该 ID 被系统保留，请换一个'
  return ''
})
const canCreate = computed(() => !creating.value && !idError.value)

watch(() => props.modelValue, (open) => {
  if (!open) return
  // 每次打开都回到默认推荐项：上次的选择没有记忆价值
  selected.value = 'reverse'
  name.value = ''
  // 给一个不冲突的默认 id（形如 bot1），用户可以改；id 创建后不可变
  const taken = new Set(bots.bots.map((b) => b.id))
  let n = 1
  while (taken.has(`bot${n}`)) n += 1
  accountId.value = `bot${n}`
})

function close() {
  if (creating.value) return
  emit('update:modelValue', false)
}

async function create() {
  if (creating.value) return
  creating.value = true
  try {
    const result = await bots.addBotConfig({
      id: accountId.value.trim(),
      name: name.value.trim(),
      conn_mode: selected.value,
    })
    notify.push('账号已添加', 'success')
    emit('update:modelValue', false)
    if (result?.id) emit('created', result.id)
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    creating.value = false
  }
}
</script>

<template>
  <v-dialog :model-value="props.modelValue" max-width="600" @update:model-value="(v: boolean) => emit('update:modelValue', v)">
    <v-card>
      <v-card-title class="d-flex align-center">
        <v-icon icon="mdi-plus-box-outline" class="mr-2" color="primary" /> 添加账号
        <v-spacer />
        <v-btn variant="text" icon="mdi-close" size="small" :disabled="creating" @click="close" />
      </v-card-title>
      <v-card-text>
        <div class="text-body-2 mb-4">你想怎么把它接进来？选一种就行。</div>
        <v-row dense>
          <v-col v-for="opt in OPTIONS" :key="opt.value" cols="12" sm="6">
            <v-card
              :variant="selected === opt.value ? 'tonal' : 'outlined'"
              :color="selected === opt.value ? 'primary' : undefined"
              class="mode-card h-100"
              @click="selected = opt.value"
            >
              <v-card-item>
                <template #prepend>
                  <v-icon :icon="opt.icon" size="26" />
                </template>
                <v-card-title class="text-body-1 font-weight-bold d-flex align-center gap-2">
                  {{ opt.title }}
                  <v-chip v-if="opt.recommended" size="x-small" variant="flat" color="primary">推荐</v-chip>
                </v-card-title>
              </v-card-item>
              <v-card-text class="pt-0">
                <div class="text-body-2">{{ opt.desc }}</div>
                <ul class="mode-tips">
                  <li v-for="tip in opt.tips" :key="tip">{{ tip }}</li>
                </ul>
              </v-card-text>
            </v-card>
          </v-col>
        </v-row>
        <v-alert
          v-if="selected === 'reverse'"
          type="info"
          variant="tonal"
          density="compact"
          class="mt-4"
        >
          创建后，把账号卡片上显示的地址原样填进 NapCat / Lagrange 的「反向 WebSocket」里，它就会连进来。
        </v-alert>

        <v-divider class="my-4" />
        <v-text-field
          v-model="name"
          label="名称"
          placeholder="给这个账号起个名字，比如「主号」"
          density="comfortable"
          hide-details="auto"
          class="mb-3"
          maxlength="32"
          autofocus
        />
        <v-text-field
          v-model="accountId"
          label="账号 ID"
          placeholder="bot1"
          :error-messages="accountId ? idError : ''"
          :hint="nameError || '程序内部用它区分账号，创建后不能修改'"
          persistent-hint
          density="comfortable"
          hide-details="auto"
          autocomplete="off"
          spellcheck="false"
        />
        <div class="text-caption mt-3" style="opacity: .7">
          连接方式创建后也不能再改，选错了删掉重加即可。
        </div>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn variant="text" :disabled="creating" @click="close">取消</v-btn>
        <v-btn
          color="primary"
          variant="tonal"
          :loading="creating"
          :disabled="!canCreate"
          prepend-icon="mdi-check"
          @click="create"
        >
          创建账号
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.mode-card {
  cursor: pointer;
  border-radius: 14px;
  height: 100%;
}

.mode-tips {
  margin: 8px 0 0;
  padding-left: 18px;
  font-size: 12px;
  line-height: 1.6;
  opacity: 0.75;
}
</style>
