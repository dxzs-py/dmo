import { createApp } from 'vue'

import App from './App.vue'
import router from './router'
import settings from './config/settings'
import pinia from './stores'
import { useThemeStore } from './stores/theme'
import { useUserStore } from './stores/user'
import { useSessionStore } from './stores/session'
import ErrorBoundary from './components/common/ErrorBoundary.vue'
import { logger } from './utils/logger'
import { setupErrorHandler } from './composables/useErrorHandler'

import './assets/css/tailwind.css'
import './assets/css/reset.css'
import './assets/css/theme.css'
import './assets/css/animations.css'

const app = createApp(App)

app.use(pinia)
app.use(router)

app.config.globalProperties.$settings = settings

const themeStore = useThemeStore()
themeStore.initTheme()

const userStore = useUserStore()
if (userStore.token) {
  userStore.getUserInfo().catch(err => {
    logger.warn('自动获取用户信息失败:', err)
  })
}

const sessionStore = useSessionStore()
sessionStore.initialize().catch(err => {
  logger.warn('初始化会话状态失败:', err)
})

app.component('ErrorBoundary', ErrorBoundary)

setupErrorHandler(app)

app.mount('#app')
