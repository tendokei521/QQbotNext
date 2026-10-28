<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useBotsStore, type BotConnMode } from '@/stores/bots'
import { useNotifyStore } from '@/stores/notify'
import { errorMessage } from '@/api/http'

/**
 * 新增账号：先选连接方式，再创建。
 *
 * 文案只描述**本程序**做什么，不提具体对端产品（NapCat / Lagrange / OneBot 实现）：
 * 那是用户自己的另一端，本程序不该假设它是什么。
 *
 * - 选项名 = 用户在协议端里要做的选择（本地监听 / 向外连接）；
 * - 一行说明 = 协议端里该选什么；
 * - 细节（地址该填什么形、OneBot 方向名）折叠在 `?` tooltip 里，不占版面。
 *
 * 为什么不直接插一张空卡片：空卡片的地址是空的，用户点「连接」才看到「连接失败」，
 * 而且监听那一侧还需要用户自己知道要填端口和 `/onebot/v11/ws` 路径。
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
  /** tooltip 里的完整解释：说清「谁建立连接」，照着做就能连上 */
  tip: string
  recommended?: boolean
}

// 名字 = 本程序在这个模式下扮演的角色；文案 = 用户要在协议端里怎么配。
// 括号里是 OneBot 的官方方向名，方便对照别人的教程。
const OPTIONS: ModeOption[] = [
  {
    value: 'reverse',
    icon: 'mdi-call-received',
    title: '本地监听（推荐）',
    desc: '本机等协议端连进来，协议端里选「WebSocket 客户端」',
    tip:
      '在 NapCat / LLOneBot 里新建「WebSocket 客户端」，URL 填本机的监听地址' +
      '（形如 ws://127.0.0.1:3001/onebot/v11/ws），保存时记得启用。' +
      '创建账号后卡片上会给出地址，可一键复制。OneBot 里这叫 reverse-ws。',
    recommended: true,
  },
  {
    value: 'forward',
    icon: 'mdi-call-made',
    title: '向外连接',
    desc: '本程序主动连协议端，协议端里选「WebSocket 服务端」',
    tip:
      '在协议端里新建「WebSocket 服务端」，把它给出的 ws 地址填到账号卡片里，' +
      '端口和路径要与协议端一致。OneBot 里这叫 forward-ws。',
  },
]

const selected = ref<BotConnMode>('reverse')
const creating = ref(false)
/** 展示名（随便改） */
const name = ref('')
/** 账号 id（唯一凭据，创建后不可改）：只允许字母/数字/下划线/连字符 */
const accountId = ref('')

const ACCOUNT_ID_RE = /^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$/
const idError = computed(() => {
  const v = accountId.value.trim()
  if (!v) return '请填写账号标识'
  if (!ACCOUNT_ID_RE.test(v)) return '只能用字母、数字、下划线、连字符，最多 32 个字符'
  if (['none', 'null', 'global', '_global'].includes(v.toLowerCase())) return '这个名字被系统保留，请换一个'
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
  <v-dialog
    :model-value="props.modelValue"
    max-width="560"
    @update:model-value="(v: boolean) => emit('update:modelValue', v)"
  >
    <v-card>
      <v-card-title class="text-h5 pa-4 pb-0 pl-6">添加账号</v-card-title>
      <v-card-text class="pa-4 pl-6">
        <div class="text-body-2 text-medium-emphasis mb-3">选一种连接方式，创建后不能改；不确定就用推荐的本地监听。</div>

        <div class="mode-list">
          <v-card
            v-for="opt in OPTIONS"
            :key="opt.value"
            variant="outlined"
            hover
            class="mode-card"
            :class="{ 'mode-card--active': selected === opt.value }"
            @click="selected = opt.value"
          >
            <v-icon :icon="opt.icon" size="22" class="mode-card__icon" />
            <div class="mode-card__text">
              <div class="mode-card__title">
                {{ opt.title }}
                <span v-if="opt.recommended" class="mode-card__rec">(推荐)</span>
                <v-tooltip :text="opt.tip" location="bottom" max-width="320">
                  <template #activator="{ props: tipProps }">
                    <v-icon
                      v-bind="tipProps"
                      icon="mdi-help-circle-outline"
                      size="15"
                      class="mode-card__help"
                      @click.stop
                    />
                  </template>
                </v-tooltip>
              </div>
              <div class="mode-card__desc">{{ opt.desc }}</div>
            </div>
            <v-icon
              v-if="selected === opt.value"
              icon="mdi-check-circle"
              color="primary"
              size="18"
              class="mode-card__check"
            />
          </v-card>
        </div>

        <v-row dense class="mt-2">
          <v-col cols="12" sm="6">
            <v-text-field
              v-model="name"
              label="名称"
              placeholder="如：主号"
              hint="随便起，只影响显示"
              persistent-hint
              density="comfortable"
              hide-details="auto"
              maxlength="32"
            />
          </v-col>
          <v-col cols="12" sm="6">
            <v-text-field
              v-model="accountId"
              label="账号标识"
              placeholder="bot1"
              :error-messages="accountId ? idError : ''"
              :hint="accountId ? '用来区分多个号，创建后不能改' : ''"
              persistent-hint
              density="comfortable"
              hide-details="auto"
              autocomplete="off"
              spellcheck="false"
            />
          </v-col>
        </v-row>
      </v-card-text>
      <v-card-actions class="px-4 pb-4">
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
/* 选择器：两个窄卡片并排，一行说明 + ? tooltip。
   选中不填充（填充块会和「推荐」chip 抢注意力），只加主色描边、浅底与右上角对勾。 */
/* 说明是整句，两列会挤成三行；单列让每条都读得顺，两个选项也都还在一屏内。 */
.mode-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.mode-card {
  position: relative;
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 12px 14px;
  border-radius: 12px;
  cursor: pointer;
  transition: border-color .15s, box-shadow .15s, background-color .15s;
}

.mode-card:hover {
  box-shadow: 0 4px 16px rgba(0, 0, 0, .06);
}

.mode-card--active {
  border-color: rgb(var(--v-theme-primary)) !important;
  background: rgba(var(--v-theme-primary), .06);
}

.mode-card__icon {
  flex: 0 0 auto;
  margin-top: 1px;
  opacity: .55;
}

.mode-card--active .mode-card__icon {
  opacity: 1;
  color: rgb(var(--v-theme-primary));
}

.mode-card__text {
  flex: 1;
  min-width: 0;
}

.mode-card__title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  font-weight: 600;
  line-height: 1.4;
}

.mode-card__rec {
  font-size: 12px;
  font-weight: 400;
  color: rgb(var(--v-theme-primary));
}

.mode-card__help {
  opacity: .45;
  cursor: help;
}

.mode-card__help:hover {
  opacity: .9;
}

.mode-card__desc {
  margin-top: 2px;
  font-size: 12px;
  line-height: 1.45;
  color: rgba(var(--v-theme-on-surface), .6);
}

.mode-card__check {
  position: absolute;
  top: 8px;
  right: 8px;
}
</style>