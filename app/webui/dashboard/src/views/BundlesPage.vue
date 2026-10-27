<script setup lang="ts">
/**
 * 套装中心（P2：工件层）。
 *
 * 能做的：导入（预检后登记）、列出现有套装、下载、从当前账号导出。
 * 暂时不能做的：应用到账号/回滚（P3）——界面上明确写出来，避免"装了没生效"。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { displayName, useBotsStore } from '@/stores/bots'
import { useBundlesStore } from '@/stores/bundles'
import { useNotifyStore } from '@/stores/notify'
import { errorMessage } from '@/api/http'
import EmptyState from '@/components/EmptyState.vue'
import BundleImportDialog from '@/components/bundles/BundleImportDialog.vue'

const router = useRouter()
const bots = useBotsStore()
const bundles = useBundlesStore()
const notify = useNotifyStore()

const importDialog = ref(false)
const exportDialog = ref(false)
const exporting = ref(false)
const exportForm = reactive({
  id: '',
  name: '',
  version: '1.0.0',
  description: '',
})

const accountName = computed(() => {
  const bot = bots.bots.find((item) => item.id === bots.currentAccountId)
  return bot ? displayName(bot) : (bots.currentAccountId || '未选择账号')
})

const exportEnabled = computed(() => Boolean(bots.currentAccountId))

function formatTime(ts: number): string {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false })
}

function formatSize(bytes: number): string {
  if (!bytes) return '—'
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

async function onImport() {
  importDialog.value = true
}

async function onDownload(id: string) {
  try {
    await bundles.download(id)
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

function openExport() {
  if (!exportEnabled.value) {
    notify.push('先在顶部选择一个账号', 'warning')
    return
  }
  exportForm.id = `account-${bots.currentAccountId}`
  exportForm.name = `${accountName.value} 的配置`
  exportForm.version = '1.0.0'
  exportForm.description = ''
  exportDialog.value = true
}

async function doExport() {
  exporting.value = true
  try {
    await bundles.exportAccount({
      id: exportForm.id.trim() || `account-${bots.currentAccountId}`,
      name: exportForm.name.trim() || '账号配置',
      version: exportForm.version.trim() || '1.0.0',
      description: exportForm.description.trim(),
    })
    notify.push('已导出套装（密钥未包含）', 'success')
    exportDialog.value = false
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    exporting.value = false
  }
}

onMounted(async () => {
  if (!bots.bots.length) await bots.fetchBots()
  await bundles.load()
})
</script>

<template>
  <div>
    <div class="app-page-header" style="align-items: center">
      <div>
        <h1 class="app-page-title">
          <v-icon icon="mdi-package-variant-closed" size="28" color="primary" class="mr-1" />
          套装中心
        </h1>
        <div class="app-page-subtitle">把一套配置打包、分享、换机迁移</div>
      </div>
      <div class="d-flex align-center gap-2">
        <v-btn variant="tonal" prepend-icon="mdi-export" :disabled="!exportEnabled" @click="openExport">
          导出当前账号
        </v-btn>
        <v-btn color="primary" prepend-icon="mdi-import" @click="onImport">导入套装</v-btn>
      </div>
    </div>

    <v-alert type="info" variant="tonal" density="compact" class="mb-4">
      <div class="text-caption">
        当前版本支持导入与导出：导入后会先做预检再登记工件；「应用到账号」在后续版本提供。
        密钥不会随包分发，导入后需要你在模型配置里重新填写。
      </div>
    </v-alert>

    <v-progress-linear v-if="bundles.loading" indeterminate color="primary" class="mb-3" />

    <v-card v-if="!bundles.list.length && !bundles.loading" variant="outlined">
      <EmptyState
        icon="mdi-package-variant"
        title="还没有套装"
        description="可以从当前账号导出第一套，或导入别人分享的 .zip 套装包。"
      >
        <v-btn color="primary" variant="tonal" prepend-icon="mdi-import" @click="onImport">导入套装</v-btn>
      </EmptyState>
    </v-card>

    <v-row v-else>
      <v-col v-for="item in bundles.list" :key="item.id" cols="12" md="6" xl="4">
        <v-card variant="outlined" class="bundle-card app-card-hover">
          <v-card-item>
            <template #prepend>
              <v-avatar color="lightprimary" size="40" rounded="lg">
                <v-icon icon="mdi-package-variant" color="primary" />
              </v-avatar>
            </template>
            <v-card-title class="text-body-1 font-weight-bold app-line-clamp-1">
              {{ item.name }}
              <v-chip size="x-small" variant="tonal" class="ml-1">v{{ item.version }}</v-chip>
            </v-card-title>
            <v-card-subtitle class="app-line-clamp-2">
              {{ item.description || '导入自本地文件' }}
            </v-card-subtitle>
          </v-card-item>
          <v-card-text class="text-caption" style="opacity: 0.7">
            <div>导入时间：{{ formatTime(item.imported_at) }}</div>
            <div>大小：{{ formatSize(item.size) }}</div>
          </v-card-text>
          <v-card-actions>
            <v-btn size="small" variant="text" prepend-icon="mdi-download" @click="onDownload(item.id)">
              下载
            </v-btn>
            <v-spacer />
            <v-btn size="small" variant="text" prepend-icon="mdi-tune" @click="router.push('/policy')">
              相关配置
            </v-btn>
          </v-card-actions>
        </v-card>
      </v-col>
    </v-row>

    <BundleImportDialog v-model="importDialog" @imported="bundles.load()" />

    <v-dialog v-model="exportDialog" max-width="520">
      <v-card>
        <v-card-title class="d-flex align-center">
          <v-icon icon="mdi-export" class="mr-2" color="primary" /> 导出为套装
        </v-card-title>
        <v-card-text>
          <div class="text-caption mb-3" style="opacity: 0.7">
            将从「{{ accountName }}」收集人格、模型连接（不含密钥）、Agent 配置与模块开关。
          </div>
          <v-text-field v-model="exportForm.id" label="套装 ID" density="comfortable" variant="outlined" />
          <v-text-field v-model="exportForm.name" label="名称" density="comfortable" variant="outlined" />
          <v-text-field v-model="exportForm.version" label="版本" density="comfortable" variant="outlined" />
          <v-text-field v-model="exportForm.description" label="描述（可选）" density="comfortable" variant="outlined" />
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="exportDialog = false">取消</v-btn>
          <v-btn color="primary" variant="tonal" :loading="exporting" @click="doExport">导出</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<style scoped>
.bundle-card {
  height: 100%;
  border-radius: 14px;
}
</style>
