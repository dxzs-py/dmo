import { createApp } from 'vue'
import { createPinia } from 'pinia'
import VueVirtualScroller from 'vue-virtual-scroller'
import 'vue-virtual-scroller/dist/vue-virtual-scroller.css'

import App from './App.vue'
import router from './router'
import settings from './settings'
import { useThemeStore } from './stores/theme'
import { useUserStore } from './stores/user'
import { useSessionStore } from './stores/session'
import ErrorBoundary from './components/ErrorBoundary.vue'

// 引入全局样式
import './assets/css/reset.css'
// 引入主题样式
import './assets/css/theme.css'
// 引入动画样式
import './assets/css/animations.css'
// 引入响应式样式
import './assets/css/responsive.css'

const app = createApp(App)

const pinia = createPinia()
app.use(pinia)
app.use(router)
app.use(VueVirtualScroller)

app.config.globalProperties.$settings = settings

// 初始化主题
const themeStore = useThemeStore()
themeStore.initTheme()

// 初始化用户状态
const userStore = useUserStore()
if (userStore.token) {
  userStore.getUserInfo().catch(err => {
    console.warn('自动获取用户信息失败:', err)
  })
}

// 初始化会话状态
const sessionStore = useSessionStore()
sessionStore.initialize().catch(err => {
  console.warn('初始化会话状态失败:', err)
})

// 使用全局错误边界包装应用
app.component('ErrorBoundary', ErrorBoundary)

// 全局错误处理
app.config.errorHandler = (err, instance, info) => {
  const errorDetails = {
    message: err?.message || '未知错误',
    stack: err?.stack,
    component: instance?.$options?.name || instance?.$options?.__name || '未知组件',
    lifecycleHook: info || '未知生命周期',
    timestamp: new Date().toISOString(),
    url: window.location.href,
    userAgent: navigator.userAgent,
  }

  console.error('❌ [Global Error]', errorDetails)

  if (import.meta.env.DEV) {
    console.group('🔍 错误详情')
    console.error('错误对象:', err)
    console.error('组件实例:', instance)
    console.error('生命周期:', info)
    console.groupEnd()
  }

  if (import.meta.env.PROD) {
    if (typeof window.__ERROR_TRACKER__ === 'function') {
      window.__ERROR_TRACKER__(errorDetails)
    }
  }
}

app.config.warnHandler = (msg, instance, trace) => {
  if (import.meta.env.DEV) {
    console.warn(`⚠️ [Vue Warning] ${msg}`, { component: instance?.$options?.name, trace })
  }
}

window.addEventListener('unhandledrejection', (event) => {
  console.error('❌ [Unhandled Promise Rejection]', event.reason)
  if (import.meta.env.PROD && typeof window.__ERROR_TRACKER__ === 'function') {
    window.__ERROR_TRACKER__({
      type: 'unhandledrejection',
      reason: event.reason?.message || event.reason,
      timestamp: new Date().toISOString(),
    })
  }
})

window.addEventListener('error', (event) => {
  if (event.error) {
    console.error('❌ [Runtime Error]', event.error)
  }
})

app.mount('#app')
