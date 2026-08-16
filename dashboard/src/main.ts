import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import pinia from './stores'
import vuetify from './plugins/vuetify'

// 本地字体（Outfit，拉丁字形；中文回退到系统字体栈）
import '@fontsource/outfit/400.css'
import '@fontsource/outfit/500.css'
import '@fontsource/outfit/600.css'
import '@fontsource/outfit/700.css'
// 全局样式：卡片规范 / 工具类 / 滚动条
import './styles/main.css'

createApp(App).use(pinia).use(router).use(vuetify).mount('#app')
