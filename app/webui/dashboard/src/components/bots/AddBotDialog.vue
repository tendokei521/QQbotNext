<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useBotsStore, type BotConnMode } from '@/stores/bots'
import { useNotifyStore } from '@/stores/notify'
import { errorMessage } from '@/api/http'

/**
 * 新增账号：先选连接方式，再创建。
 *
 * 为什么不直接插一张空卡片：空卡片的地址是空的，用户点「连接」才看到「连接失败」，
 * 而且反向账号还需要用户自己知道要填端口和 `/onebot/v11/ws` 路径。先把两条路摆出来
 * 选一次，创建时后端按模式给好默认地址，新卡片一出生就能用。
 */
const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'created', index: number): void
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
    title: '反向 WebSocket',
    desc: '本框架监听端口，由 OneBot 实现连进来',
    tips: ['适合 NapCat / Lagrange / LLOneBot', '框架与 OneBot 实现分开部署时也能用'],
    recommended: true,
  },
  {
    value: 'forward',
    icon: 'mdi-lan',
    title: '正向 WebSocket',
    desc: '框架连到 OneBot 实现已开放的 ws 地址',
    tips: ['OneBot 实现里已开好 ws 服务端', '地址形如 ws://127.0.0.1:3001/'],
  },
]

const selected = ref<BotConnMode>('reverse')
const creating = ref(false)

watch(() => props.modelValue, (open) => {
  // 每次打开都回到默认推荐项：上次的选择没有记忆价值
  if (open) selected.value = 'reverse'
})

const current = computed(() => OPTIONS.find((o) => o.value === selected.value) ?? OPTIONS[0])

function close() {
  if (creating.value) return
  emit('update:modelValue', false)
}

async function create() {
  if (creating.value) return
  creating.value = true
  try {
    const result = await bots.addBotConfig({ conn_mode: selected.value })
    const index = Number(result?.index ?? -1)
    notify.push(index >= 0 ? `已添加账号 #${index}` : '已添加账号', 'success')
    emit('update:modelValue', false)
    if (index >= 0) emit('created', index)
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
        <div class="text-body-2 mb-3">选择连接方式，创建后仍可在账号卡片上修改。</div>
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
          创建后把卡片上的「反向监听地址」填进 OneBot 实现的反向 WebSocket 设置里，连接即建立。
        </v-alert>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn variant="text" :disabled="creating" @click="close">取消</v-btn>
        <v-btn color="primary" variant="tonal" :loading="creating" prepend-icon="mdi-check" @click="create">
          创建{{ current?.title ? `「${current.title}」账号` : '账号' }}
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
