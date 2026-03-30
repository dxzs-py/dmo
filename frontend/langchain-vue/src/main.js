import { createApp } from 'vue'
import { createPinia } from 'pinia'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'

import App from './App.vue'
import router from './router'
import settings from './settings'
import { useThemeStore } from './stores/theme'
import ErrorBoundary from './components/ErrorBoundary.vue'

// 引入 Element Plus
import ElementPlus from 'element-plus'
// 引入 Element Plus 核心样式
import 'element-plus/dist/index.css'
// 引入全局样式
import '../static/css/reset.css'
// 引入主题样式
import '../static/css/theme.css'
// 引入动画样式
import '../static/css/animations.css'
// 引入响应式样式
import '../static/css/responsive.css'

const app = createApp(App)

// 注册所有 Element Plus 图标
for (const [key, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(key, component)
}

const pinia = createPinia()
app.use(pinia)
app.use(router)
// 启用 Element Plus
app.use(ElementPlus)

app.config.globalProperties.$settings = settings

// 初始化主题
const themeStore = useThemeStore()
themeStore.initTheme()

// 使用全局错误边界包装应用
app.component('ErrorBoundary', ErrorBoundary)

// 全局错误处理
app.config.errorHandler = (err, instance, info) => {
  console.error('全局错误:', err, info)
}

app.mount('#app')
