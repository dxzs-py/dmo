import { fileURLToPath, URL } from 'node:url'

import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import vueDevTools from 'vite-plugin-vue-devtools'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'
import viteCompression from 'vite-plugin-compression'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const isDev = mode === 'development'

  return {
    plugins: [
      vue(),
      isDev ? vueDevTools() : undefined,
      AutoImport({
        resolvers: [ElementPlusResolver()],
        imports: ['vue', 'vue-router', 'pinia'],
        dts: 'src/auto-imports.d.ts',
      }),
      Components({
        resolvers: [ElementPlusResolver()],
        dts: 'src/components.d.ts',
      }),
      !isDev ? viteCompression({
        algorithm: 'gzip',
        threshold: 10240,
        deleteOriginFile: false,
      }) : undefined,
    ].filter(Boolean),
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url)),
      },
    },
    server: {
      host: env.VITE_DEV_HOST || 'localhost',
      port: parseInt(env.VITE_DEV_PORT) || 8080,
      open: env.VITE_DEV_OPEN === 'true',
      proxy: {
        '/api': {
          target: env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000',
          changeOrigin: true,
          ws: true,
        },
        '/health': {
          target: env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000',
          changeOrigin: true,
        },
        '/admin': {
          target: env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000',
          changeOrigin: true,
        },
      },
    },
    build: {
      rollupOptions: {
        output: {
          manualChunks(id) {
            if (!id.includes('node_modules')) return
            if (id.includes('@element-plus/icons-vue')) return 'ep-icons'
            if (id.includes('element-plus/es/components/') || id.includes('@element-plus/es/components/')) return 'ep-components'
            if (id.includes('element-plus') || id.includes('@element-plus')) return 'element-plus'
            if (id.includes('vue/') || id.includes('vue-router') || id.includes('pinia') || id.includes('@vue/')) return 'vue-vendor'
            if (id.includes('marked') || id.includes('highlight.js') || id.includes('dompurify')) return 'markdown'
            if (id.includes('axios')) return 'axios'
            if (id.includes('zod') || id.includes('nanoid') || id.includes('@vueuse/')) return 'utils-vendor'
          },
        },
      },
      chunkSizeWarningLimit: 750,
      reportCompressedSize: !isDev,
      sourcemap: isDev,
      minify: !isDev ? 'terser' : false,
      terserOptions: isDev ? undefined : {
        compress: {
          drop_console: true,
          drop_debugger: true,
          pure_funcs: ['console.log', 'console.info', 'console.debug'],
        },
      },
    },
    optimizeDeps: {
      include: [
        'vue',
        'vue-router',
        'pinia',
        'element-plus',
        '@element-plus/icons-vue',
        'axios',
        '@vueuse/core',
      ],
    },
  }
})
