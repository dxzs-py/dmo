import { createApp } from 'vue'
import { createPinia } from 'pinia'

import App from './App.vue'
import router from './router'
import settings from './settings'


// 引入 Element Plus
import ElementPlus from 'element-plus'
// 引入 Element Plus 核心样式
import 'element-plus/dist/index.css'
// 引入全局样式
import '../static/css/reset.css'

const app = createApp(App)

app.use(createPinia())
app.use(router)
// 启用 Element Plus
app.use(ElementPlus)


app.config.globalProperties.$settings = settings


app.mount('#app')
