<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useWebuiStore } from '@/stores/webui'
import { useNotifyStore } from '@/stores/notify'
import { errorMessage } from '@/api/http'
import PromptPresetEditorDialog from '@/components/settings/PromptPresetEditorDialog.vue'

const router = useRouter()
const webui = useWebuiStore()
const notify = useNotifyStore()
const promptDialog = ref(false)

/** 界面构建戳：排查「改了没生效」时用。 */
const buildStamp = __BUILD_STAMP__

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
        <div class="app-page-subtitle">界面偏好、系统信息与备份入口</div>
      </div>
    </div>

    <v-row>
      <v-col cols="12" lg="6">
        <v-card variant="outlined" class="mb-4">
          <v-card-title class="d-flex align-center">
            <v-icon icon="mdi-rocket-launch-outline" class="mr-2" color="primary" /> 配置向导
          </v-card-title>
          <v-card-text>
            <div class="text-caption mb-3" style="opacity: 0.7">
              再走一遍「连接账号 → 配置模型 → 人格设定」。会更新默认账号；已有账号请到账号管理里修改。
            </div>
            <v-btn color="primary" variant="tonal" prepend-icon="mdi-open-in-new" @click="router.push('/setup')">
              打开向导
            </v-btn>
          </v-card-text>
        </v-card>

        <v-card variant="outlined" class="mb-4">
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
                <v-list-item-title>访问令牌</v-list-item-title>
                <v-list-item-subtitle class="text-wrap">由启动配置决定，见启动控制台；这里不显示明文</v-list-item-subtitle>
              </v-list-item>
              <v-list-item>
                <v-list-item-title>界面版本</v-list-item-title>
                <v-list-item-subtitle class="text-wrap">{{ buildStamp }}</v-list-item-subtitle>
              </v-list-item>
            </v-list>
          </v-card-text>
        </v-card>

        <v-card variant="outlined" class="mb-4">
          <v-card-title class="d-flex align-center">
            <v-icon icon="mdi-download-outline" class="mr-2" color="primary" /> 备份与恢复
          </v-card-title>
          <v-card-text>
            <div class="text-caption mb-3" style="opacity: 0.7">
              导出与导入都在对应页面里完成，这里只做入口汇总。
            </div>
            <div class="d-flex flex-wrap gap-2">
              <v-btn size="small" variant="tonal" prepend-icon="mdi-package-variant-closed" @click="router.push('/bundles')">
                配置包
              </v-btn>
              <v-btn size="small" variant="tonal" prepend-icon="mdi-chat-outline" @click="router.push('/sessions')">
                聊天记录
              </v-btn>
              <v-btn size="small" variant="tonal" prepend-icon="mdi-file-export-outline" @click="router.push('/logs')">
                日志文件
              </v-btn>
            </div>
          </v-card-text>
        </v-card>

        <v-card variant="outlined">
          <v-card-title class="d-flex align-center">
            <v-icon icon="mdi-console" class="mr-2" color="primary" /> 日志显示
          </v-card-title>
          <v-card-text class="d-flex align-center flex-wrap gap-2">
            <div class="text-caption" style="opacity: 0.7">
              级别、显示行数和技术日志开关都在日志页调整，改完即时生效。
            </div>
            <v-spacer />
            <v-btn size="small" color="primary" variant="tonal" prepend-icon="mdi-open-in-new" @click="router.push('/logs')">
              打开日志页
            </v-btn>
          </v-card-text>
        </v-card>
      </v-col>

      <v-col cols="12" lg="6">
        <v-card variant="outlined" class="mb-4">
          <v-card-title class="d-flex align-center">
            <v-icon icon="mdi-text-box-edit-outline" class="mr-2" color="primary" /> 系统提示词
          </v-card-title>
          <v-card-text class="d-flex align-center flex-wrap gap-2">
            <div class="text-caption" style="opacity: 0.7">
              本地修改「聊天 / 角色扮演 / 助手」三套提示词正文（全局生效）。
            </div>
            <v-spacer />
            <v-btn size="small" color="primary" variant="tonal" prepend-icon="mdi-pencil" @click="promptDialog = true">
              编辑系统提示词
            </v-btn>
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
          </v-card-title>
          <v-card-text class="d-flex align-center flex-wrap gap-2">
            <div class="text-caption" style="opacity: 0.7">
              插件启停与恢复都在「功能插件」页，这里不再重复入口。
            </div>
            <v-spacer />
            <v-btn size="small" color="primary" variant="tonal" prepend-icon="mdi-open-in-new" @click="router.push('/modules')">
              打开功能插件
            </v-btn>
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
                <v-list-item-title>深色 / 浅色主题</v-list-item-title>
                <v-list-item-subtitle>用顶栏右侧的太阳 / 月亮按钮切换</v-list-item-subtitle>
              </v-list-item>
            </v-list>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>
    <PromptPresetEditorDialog v-model="promptDialog" />
  </div>
</template>
