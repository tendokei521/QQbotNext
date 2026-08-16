import 'vuetify/styles'
import { createVuetify } from 'vuetify'
import { md3 } from 'vuetify/blueprints'
import { aliases, mdi } from 'vuetify/iconsets/mdi'
import '@mdi/font/css/materialdesignicons.css'

// ==================== 主题令牌（对齐 AstrBot dashboard 的设计语言） ====================
// 来源：1/AstrBot-master/dashboard/src/theme/{LightTheme,DarkTheme}.ts
// 浅色 / 暗色双主题，主色为 AstrBot 同款蓝 #3c96ca / #5ba4d4

const lightTheme = {
  dark: false,
  colors: {
    primary: '#3c96ca',
    secondary: '#2f86bd',
    info: '#03c9d7',
    success: '#00c853',
    accent: '#FFAB91',
    warning: '#ffc107',
    error: '#f44336',
    lightprimary: '#eef2f6',
    lightsecondary: '#e8f3fa',
    lightsuccess: '#b9f6ca',
    lighterror: '#f9d8d8',
    lightwarning: '#fff8e1',
    'primary-text': '#1b1c1d',
    'secondary-text': '#000000aa',
    darkprimary: '#1565c0',
    darksecondary: '#236b99',
    'border-light': '#d0d0d0',
    border: '#d0d0d0',
    'input-border': '#787878',
    'container-bg': '#fffffff4',
    surface: '#fff',
    'on-surface-variant': '#fff',
    background: '#ffffff',
    overlay: '#ffffffaa',
    'code-bg': '#ececec',
    'pre-bg': 'rgb(249, 249, 249)',
    code: 'rgb(13, 13, 13)',
    'chat-message-bubble': '#e7ebf4',
  },
  variables: {
    'border-color': '#1e88e5',
  },
}

const darkTheme = {
  dark: true,
  colors: {
    primary: '#5ba4d4',
    secondary: '#4a95c4',
    info: '#03c9d7',
    success: '#52c41a',
    accent: '#FFAB91',
    warning: '#faad14',
    error: '#ff4d4f',
    lightprimary: '#1a2e3d',
    lightsecondary: '#1a2e3d',
    lightsuccess: '#1a3a1a',
    lighterror: '#3d1a1a',
    lightwarning: '#3d351a',
    'primary-text': '#e8eaed',
    'secondary-text': '#ffffffdd',
    darkprimary: '#3a8ab8',
    darksecondary: '#3a8ab8',
    'border-light': '#3a3a3a',
    border: '#333333ee',
    'input-border': '#787878',
    'container-bg': '#1a1a1a',
    surface: '#242424',
    'on-surface-variant': '#e0e0e0',
    background: '#1a1a1a',
    overlay: '#111111aa',
    'code-bg': '#282833',
    'pre-bg': 'rgb(23, 23, 23)',
    code: '#ffffffdd',
    'chat-message-bubble': '#2d2e30',
  },
  variables: {
    'border-color': '#3c96ca',
  },
}

export default createVuetify({
  blueprint: md3,
  theme: {
    defaultTheme: 'light',
    themes: {
      light: lightTheme as any,
      dark: darkTheme as any,
    },
  },
  icons: {
    defaultSet: 'mdi',
    aliases,
    sets: { mdi },
  },
  defaults: {
    VBtn: {
      style: 'text-transform: none; letter-spacing: 0;',
      rounded: 'lg',
    },
    VCard: {
      rounded: 'xl',
    },
    VTextField: {
      variant: 'outlined',
      density: 'comfortable',
    },
    VSelect: {
      variant: 'outlined',
      density: 'comfortable',
    },
    VTextarea: {
      variant: 'outlined',
      density: 'comfortable',
    },
    VDialog: {
      rounded: 'xl',
    },
    VSwitch: {
      color: 'primary',
      inset: true,
    },
  },
})
