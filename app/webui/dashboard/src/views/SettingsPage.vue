<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useModulesStore, type UninstalledModuleData } from '@/stores/modules'
import { useThemeStore } from '@/stores/theme'
import { useWebuiStore } from '@/stores/webui'
import { useNotifyStore } from '@/stores/notify'
import { errorMessage } from '@/api/http'
import LogExportDialog from '@/components/log/LogExportDialog.vue'

const router = useRouter()

const themeStore = useThemeStore()
const webui = useWebuiStore()
const modules = useModulesStore()
const notify = useNotifyStore()

/** 界面构建戳：从侧边栏挪到这里，排查「改了没生效」时用。 */
const buildStamp = __BUILD_STAMP__

/** 日志级别的中文名（后端仍按 debug/info/warning/error 存储）。 */
const LOG_LEVELS = [
  { value: 'debug', title: '调试（最详细）' },
  { value: 'info', title: '普通' },
  { value: 'warning', title: '警告' },
  { value: 'error', title: '错误' },
]

/** 已卸载记录的来源：新后端为 builtin/installed，旧记录仍是 local/zip。 */
function isThirdParty(source?: string | null): boolean {
  return source === 'installed' || source === 'zip'
}

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
    notify.push(`插件「${item.display_name}」已恢复`, 'success')
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
        <div class="app-page-subtitle">界面、日志与插件偏好</div>
      </div>
    </div>

    <v-row>
      <v-col cols="12" lg="6">
        <v-card variant="outlined" class="mb-4">
          <v-card-title class="d-flex align-center">
            <v-icon icon="mdi-rocket-launch-outline" class="mr-2" color="primary" /> 配置向导
          </v-card-title>
          <v-card-text class="d-flex align-center flex-wrap gap-2">
            <div class="text-caption" style="opacity: 0.7">
              再走一遍「连接账号 → 配置模型 → 人格设定」，每一步都可以跳过。
            </div>
            <v-spacer />
            <v-btn color="primary" variant="tonal" prepend-icon="mdi-open-in-new" @click="router.push('/setup')">
              打开向导
            </v-btn>
          </v-card-text>
        </v-card>

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
            <v-icon icon="mdi-flask-outline" class="mr-2" color="warning" /> 高级选项
          </v-card-title>
          <v-card-text>
            <v-switch
              v-model="showExperimental"
              label="显示高级工具与调试入口"
              color="warning"
              density="compact"
              hide-details
            />
            <div class="text-caption mt-1" style="color: rgba(var(--v-theme-on-surface), 0.55)">
              面向进阶用户：开启后侧边栏会出现工具调试入口，日常使用不需要打开。
            </div>
          </v-card-text>
        </v-card>

        <v-card variant="outlined" class="mb-4">
          <v-card-title class="d-flex align-center">
            <v-icon icon="mdi-package-variant" class="mr-2" color="primary" /> 插件管理
            <v-spacer />
            <v-btn size="small" variant="tonal" prepend-icon="mdi-package-removed" @click="openUninstalledDialog">
              已停用的插件
            </v-btn>
          </v-card-title>
          <v-card-text class="text-caption" style="color: rgba(var(--v-theme-on-surface), 0.55)">
            停用插件只会隐藏它的功能，不会删除文件、配置或数据；随时可以在这里恢复。
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
                <v-list-item-subtitle>轻量、多账号的 QQ 机器人管理后台</v-list-item-subtitle>
              </v-list-item>
              <v-list-item>
                <v-list-item-title>界面版本</v-list-item-title>
                <v-list-item-subtitle>{{ buildStamp }}</v-list-item-subtitle>
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
              v-for="lv in LOG_LEVELS"
              :key="lv.value"
              v-model="levels"
              :label="lv.title"
              :value="lv.value"
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
              label="显示技术日志"
              color="primary"
              density="compact"
              class="mt-2"
              hide-details
            />
            <div class="text-caption mt-2" style="color: rgba(var(--v-theme-on-surface), 0.55)">
              保存后日志页即时生效。技术日志包含内部细节，仅在排查问题时打开。
            </div>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <LogExportDialog v-model="exportDialog" />

    <v-dialog v-model="uninstalledDialog" max-width="620">
      <v-card>
        <v-card-title class="d-flex align-center">
          <v-icon icon="mdi-package-removed" class="mr-2" color="primary" /> 已停用的插件
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
                {{ isThirdParty(item.source) ? '第三方插件' : '系统自带' }}
              </v-list-item-subtitle>
              <template #append>
                <v-btn size="small" variant="tonal" prepend-icon="mdi-arrow-up-bold-circle" @click="reinstallModule(item)">
                  恢复
                </v-btn>
              </template>
            </v-list-item>
            <v-list-item v-if="!modules.uninstalledModules.length">
              <v-list-item-title class="text-caption text-center py-3" style="color: rgba(var(--v-theme-on-surface), 0.45)">
                没有已停用的插件
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
