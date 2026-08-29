<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useModulesStore, type UninstalledModuleData } from '@/stores/modules'
import { useThemeStore } from '@/stores/theme'
import { useWebuiStore } from '@/stores/webui'
import { useNotifyStore } from '@/stores/notify'
import { errorMessage } from '@/api/http'
import LogExportDialog from '@/components/log/LogExportDialog.vue'

const themeStore = useThemeStore()
const webui = useWebuiStore()
const modules = useModulesStore()
const notify = useNotifyStore()

const saving = ref(false)
const exportDialog = ref(false)
const uninstalledDialog = ref(false)
const uninstalledLoading = ref(false)
// 可写 computed：webui.load() 覆盖配置后仍绑定到最新值
const levels = computed<string[]>({
  get: () => webui.config.logs.visible_levels,
  set: (v) => {
    webui.config.logs.visible_levels = v
  },
})
const maxLines = computed<number>({
  get: () => webui.config.logs.max_lines,
  set: (v) => {
    webui.config.logs.max_lines = v
  },
})
const showRawLogs = computed<boolean>({
  get: () => webui.config.logs.show_raw_logs,
  set: (v) => {
    webui.config.logs.show_raw_logs = v
  },
})

async function saveLogs() {
  saving.value = true
  try {
    await webui.saveLogs({
      show_raw_logs: showRawLogs.value,
      visible_levels: levels.value,
      max_lines: maxLines.value,
    })
    notify.push('日志设置已保存', 'success')
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    saving.value = false
  }
}

const showExperimental = computed<boolean>({
  get: () => !!webui.config.experimental?.show_experimental,
  set: (v) => {
    webui.config.experimental.show_experimental = v
    webui.saveExperimental({ show_experimental: v }).catch((err) => {
      notify.push(errorMessage(err), 'error')
    })
  },
})

async function openUninstalledDialog() {
  uninstalledDialog.value = true
  uninstalledLoading.value = true
  try {
    await modules.loadUninstalled()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    uninstalledLoading.value = false
  }
}

async function reinstallModule(item: UninstalledModuleData) {
  try {
    await modules.install(item.module_name)
    notify.push(`模块 ${item.display_name} 已恢复安装`, 'success')
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

onMounted(() => {
  if (!webui.config.logs.max_lines) webui.load()
})
</script>

<template>
  <div>
    <div class="app-page-header">
      <div>
        <h1 class="app-page-title">设置</h1>
        <div class="app-page-subtitle">主题、日志与控制台偏好</div>
      </div>
    </div>

    <v-row>
      <v-col cols="12" lg="6">
        <v-card variant="outlined" class="mb-4">
          <v-card-title class="d-flex align-center">
            <v-icon icon="mdi-theme-light-dark" class="mr-2" color="primary" /> 主题外观
          </v-card-title>
          <v-card-text>
            <div class="d-flex gap-3">
              <v-card
                variant="outlined"
                class="theme-option"
                :class="{ active: !themeStore.isDark }"
                @click="themeStore.set('light')"
              >
                <v-card-text class="text-center pa-4">
                  <v-icon icon="mdi-white-balance-sunny" size="32" :color="!themeStore.isDark ? 'primary' : undefined" />
                  <div class="mt-2 font-weight-medium">浅色模式</div>
                </v-card-text>
              </v-card>
              <v-card
                variant="outlined"
                class="theme-option"
                :class="{ active: themeStore.isDark }"
                @click="themeStore.set('dark')"
              >
                <v-card-text class="text-center pa-4">
                  <v-icon icon="mdi-weather-night" size="32" :color="themeStore.isDark ? 'primary' : undefined" />
                  <div class="mt-2 font-weight-medium">暗色模式</div>
                </v-card-text>
              </v-card>
            </div>
            <div class="text-caption mt-3" style="color: rgba(var(--v-theme-on-surface), 0.55)">
              选择后立即生效并保存在本地浏览器
            </div>
          </v-card-text>
        </v-card>

        <v-card variant="outlined" class="mb-4">
          <v-card-title class="d-flex align-center">
            <v-icon icon="mdi-flask-outline" class="mr-2" color="warning" /> 实验性选项
          </v-card-title>
          <v-card-text>
            <v-switch
              v-model="showExperimental"
              label="显示实验性选项"
              color="warning"
              density="compact"
              hide-details
            />
            <div class="text-caption mt-2" style="color: rgba(var(--v-theme-on-surface), 0.55)">
              开启后，导航中会显示「配置档案」，并在 Agent 面板显示「知识库 / MCP」等实验性配置入口
            </div>
          </v-card-text>
        </v-card>

        <v-card variant="outlined" class="mb-4">
          <v-card-title class="d-flex align-center">
            <v-icon icon="mdi-package-variant" class="mr-2" color="primary" /> 插件管理
            <v-spacer />
            <v-btn size="small" variant="tonal" prepend-icon="mdi-package-removed" @click="openUninstalledDialog">
              已卸载模块
            </v-btn>
          </v-card-title>
          <v-card-text class="text-caption" style="color: rgba(var(--v-theme-on-surface), 0.55)">
            软卸载只屏蔽模块加载与显示，不会删除插件文件、配置或数据；可在此恢复安装。
          </v-card-text>
        </v-card>

        <v-card variant="outlined">
          <v-card-title class="d-flex align-center">
            <v-icon icon="mdi-information-outline" class="mr-2" color="primary" /> 关于
          </v-card-title>
          <v-card-text>
            <v-list density="compact">
              <v-list-item>
                <v-list-item-title>QQBot Next 管理后台</v-list-item-title>
                <v-list-item-subtitle>基于 OneBot 协议的多账号 QQ 机器人框架</v-list-item-subtitle>
              </v-list-item>
              <v-list-item>
                <v-list-item-title>Dashboard 版本</v-list-item-title>
                <v-list-item-subtitle>Vue 3 + Vuetify 3 重构版 1.0.0</v-list-item-subtitle>
              </v-list-item>
            </v-list>
          </v-card-text>
        </v-card>
      </v-col>

      <v-col cols="12" lg="6">
        <v-card variant="outlined">
          <v-card-title class="d-flex align-center">
            <v-icon icon="mdi-filter-outline" class="mr-2" color="primary" /> 日志显示设置
            <v-spacer />
            <v-btn size="small" variant="tonal" prepend-icon="mdi-file-export-outline" @click="exportDialog = true">
              导出日志
            </v-btn>
            <v-btn size="small" color="primary" variant="tonal" prepend-icon="mdi-content-save" class="ml-2" :loading="saving" @click="saveLogs">
              保存
            </v-btn>
          </v-card-title>
          <v-card-text>
            <div class="text-subtitle-2 mb-1">显示级别</div>
            <v-checkbox
              v-for="lv in ['debug', 'info', 'warning', 'error']"
              :key="lv"
              v-model="levels"
              :label="lv"
              :value="lv"
              density="compact"
              hide-details
              color="primary"
            />
            <v-text-field
              v-model.number="maxLines"
              label="显示行数"
              type="number"
              min="10"
              max="200"
              density="comfortable"
              hide-details
              class="mt-2"
            />
            <v-switch
              v-model="showRawLogs"
              label="显示原始日志"
              color="primary"
              density="compact"
              class="mt-2"
              hide-details
            />
            <div class="text-caption mt-2" style="color: rgba(var(--v-theme-on-surface), 0.55)">
              修改后需保存；日志页面实时生效；默认关闭，开启后显示完整技术日志
            </div>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <LogExportDialog v-model="exportDialog" />

    <v-dialog v-model="uninstalledDialog" max-width="620">
      <v-card>
        <v-card-title class="d-flex align-center">
          <v-icon icon="mdi-package-removed" class="mr-2" color="primary" /> 已卸载模块
          <v-spacer />
          <v-btn icon="mdi-close" variant="text" size="small" @click="uninstalledDialog = false" />
        </v-card-title>
        <v-card-text>
          <v-progress-linear v-if="uninstalledLoading" indeterminate color="primary" />
          <v-list v-else density="compact">
            <v-list-item
              v-for="item in modules.uninstalledModules"
              :key="item.module_name"
            >
              <v-list-item-title>{{ item.display_name }}</v-list-item-title>
              <v-list-item-subtitle>
                {{ item.module_name }} · {{ item.source === 'zip' ? 'zip 插件' : '本地模块' }} · v{{ item.version || '0.0.0' }}
              </v-list-item-subtitle>
              <template #append>
                <v-btn size="small" variant="tonal" prepend-icon="mdi-arrow-up-bold-circle" @click="reinstallModule(item)">
                  安装
                </v-btn>
              </template>
            </v-list-item>
            <v-list-item v-if="!modules.uninstalledModules.length">
              <v-list-item-title class="text-caption text-center py-3" style="color: rgba(var(--v-theme-on-surface), 0.45)">
                暂无已卸载模块
              </v-list-item-title>
            </v-list-item>
          </v-list>
        </v-card-text>
      </v-card>
    </v-dialog>
  </div>
</template>

<style scoped>
.theme-option {
  flex: 1;
  cursor: pointer;
  border-radius: 14px;
  transition: border-color 0.2s, transform 0.15s;
}

.theme-option:hover {
  transform: translateY(-1px);
}

.theme-option.active {
  border-color: rgb(var(--v-theme-primary));
  background: rgba(var(--v-theme-primary), 0.06);
}
</style>
