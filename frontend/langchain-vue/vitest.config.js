import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vitest/config'

// 测试专用配置：不复用 vite.config.js 的生产插件（vue/compression/auto-import 等），
// 仅提供 `@/` 别名解析与用例收集规则。被测均为纯逻辑工具模块，node 环境即可。
export default defineConfig({
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    environment: 'node',
    include: ['src/**/__tests__/**/*.test.js'],
  },
})
