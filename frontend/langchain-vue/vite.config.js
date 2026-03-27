import { fileURLToPath, URL } from 'node:url'

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import vueDevTools from 'vite-plugin-vue-devtools'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue(), vueDevTools()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: 'www.langchain.cn', // 绑定你的前端域名
    port: 8080, // 端口号（和旧配置一致，注意：80端口需要管理员权限运行终端）
    open: true, // 自动打开浏览器（对应旧的 autoOpenBrowser: true）
  },
})
