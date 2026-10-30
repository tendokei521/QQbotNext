<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useWebuiStore } from '@/stores/webui'
import { useNotifyStore } from '@/stores/notify'
import { errorMessage } from '@/api/http'
import PromptPresetEditorDialog from '@/components/settings/PromptPresetEditorDialog.vue'

const router = useRouter()
const route = useRoute()
const webui = useWebuiStore()
const notify = useNotifyStore()
const promptDialog = ref(false)

/** 从「AI 对话面板 → 提示词风格」跳过来时直接打开编辑弹窗（query: ?prompt-presets=1） */
onMounted(() => {
  if (!route.query['prompt-presets']) return
  promptDialog.value = true
  const query = { ...route.query }
  delete query['prompt-presets']
  router.replace({ query })
})

/** 应用版本：界面只展示 package.json 的版本，不展示 commit 戳 */
const appVersion = __APP_VERSION__

/** 访问入口：当前浏览器实际打开的地址（含端口），排查连不上时最常被问到的信息 */
const accessAddress = typeof location !== 'undefined' ? location.origin : ''
const accessPort =
  typeof location !== 'undefined' && location.port ? location.port : '默认端口'

const showExperimental = computed<boolean>({
  get: () => !!webui.config.experimental?.show_experimental,
  set: (v) => {
    webui.config.experimental.show_experimental = v
    webui.saveExperimental({ show_experimental: v }).catch((err) => {
      notify.push(errorMessage(err), 'error')
    })
  },
})
</script>

<template>
  <div>
    <div class="app-page-header">
      <div>
        <h1 class="app-page-title">设置</h1>
        <div class="app-page-subtitle">界面偏好与系统信息</div>
      </div>
    </div>

    <div class="app-card-grid app-card-grid--wide">
      <v-card variant="outlined" class="settings-card">
        <v-card-title class="d-flex align-center">
          <v-icon icon="mdi-rocket-launch-outline" class="mr-2" color="primary" /> 配置向导
        </v-card-title>
        <v-card-text class="d-flex align-center flex-wrap gap-2">
          <span class="app-muted">重新设置账号、模型与人格</span>
          <v-spacer />
          <v-btn size="small" color="primary" variant="tonal" prepend-icon="mdi-open-in-new" @click="router.push('/setup')">
            打开向导
          </v-btn>
        </v-card-text>
      </v-card>

      <v-card variant="outlined" class="settings-card">
        <v-card-title class="d-flex align-center">
          <v-icon icon="mdi-information-outline" class="mr-2" color="primary" /> 系统信息
        </v-card-title>
        <v-card-text>
          <v-list density="compact">
            <v-list-item>
              <v-list-item-title>访问地址</v-list-item-title>
              <v-list-item-subtitle class="text-wrap">{{ accessAddress }}</v-list-item-subtitle>
            </v-list-item>
            <v-list-item>
              <v-list-item-title>端口</v-list-item-title>
              <v-list-item-subtitle class="text-wrap">{{ accessPort }}</v-list-item-subtitle>
            </v-list-item>
            <v-list-item>
              <v-list-item-title>界面版本</v-list-item-title>
              <v-list-item-subtitle class="text-wrap">{{ appVersion }}</v-list-item-subtitle>
            </v-list-item>
            <v-list-item>
              <v-list-item-title>主题</v-list-item-title>
              <v-list-item-subtitle class="text-wrap">深色 / 浅色，用顶栏右侧按钮切换</v-list-item-subtitle>
            </v-list-item>
          </v-list>
        </v-card-text>
      </v-card>

      <v-card variant="outlined" class="settings-card">
        <v-card-title class="d-flex align-center">
          <v-icon icon="mdi-download-outline" class="mr-2" color="primary" /> 备份与恢复
        </v-card-title>
        <v-card-text class="d-flex flex-wrap gap-2">
          <v-btn size="small" variant="tonal" prepend-icon="mdi-package-variant-closed" @click="router.push('/bundles')">
            配置包
          </v-btn>
          <v-btn size="small" variant="tonal" prepend-icon="mdi-chat-outline" @click="router.push('/sessions')">
            聊天记录
          </v-btn>
          <v-btn size="small" variant="tonal" prepend-icon="mdi-file-export-outline" @click="router.push('/logs')">
            日志文件
          </v-btn>
        </v-card-text>
      </v-card>

      <v-card variant="outlined" class="settings-card">
        <v-card-title class="d-flex align-center">
          <v-icon icon="mdi-console" class="mr-2" color="primary" /> 日志
        </v-card-title>
        <v-card-text class="d-flex align-center flex-wrap gap-2">
          <span class="app-muted">级别、行数与导出在日志页</span>
          <v-spacer />
          <v-btn size="small" color="primary" variant="tonal" prepend-icon="mdi-open-in-new" @click="router.push('/logs')">
            打开日志页
          </v-btn>
        </v-card-text>
      </v-card>

      <v-card variant="outlined" class="settings-card">
        <v-card-title class="d-flex align-center">
          <v-icon icon="mdi-text-box-edit-outline" class="mr-2" color="primary" /> 系统提示词
        </v-card-title>
        <v-card-text class="d-flex align-center flex-wrap gap-2">
          <span class="app-muted">修改「聊天 / 角色扮演 / 助手」三套提示词</span>
          <v-spacer />
          <v-btn size="small" color="primary" variant="tonal" prepend-icon="mdi-pencil" @click="promptDialog = true">
            编辑提示词
          </v-btn>
        </v-card-text>
      </v-card>

      <v-card variant="outlined" class="settings-card">
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
          <div class="app-muted mt-1">开启后侧边栏显示工具调试入口</div>
        </v-card-text>
      </v-card>

      <v-card variant="outlined" class="settings-card">
        <v-card-title class="d-flex align-center">
          <v-icon icon="mdi-package-variant" class="mr-2" color="primary" /> 功能插件
        </v-card-title>
        <v-card-text class="d-flex align-center flex-wrap gap-2">
          <span class="app-muted">安装、启停与恢复都在功能插件页</span>
          <v-spacer />
          <v-btn size="small" color="primary" variant="tonal" prepend-icon="mdi-open-in-new" @click="router.push('/modules')">
            打开功能插件
          </v-btn>
        </v-card-text>
      </v-card>
    </div>
    <PromptPresetEditorDialog v-model="promptDialog" />
  </div>
</template>

<style scoped>
/* 卡片等高：同一行里内容少的卡不再比旁边的矮一截 */
.settings-card {
  height: 100%;
}
</style>
