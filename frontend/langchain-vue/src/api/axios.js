import axios from 'axios'
import settings from '../config/settings'
import { useUserStore } from '@/stores/user'
import { useLoadingStore } from '@/stores/loading'
import { toCamelCase, toSnakeCase } from '@/utils/sessionTransformers'

const apiClient = axios.create({
  baseURL: settings.apiBaseUrl,
  timeout: 300000,
  headers: { 'Content-Type': 'application/json' },
})

apiClient.interceptors.request.use(
  (config) => {
    const userStore = useUserStore()
    if (userStore.token) {
      config.headers.Authorization = `Bearer ${userStore.token}`
    }
    if (!config.skipLoading) {
      const loadingStore = useLoadingStore()
      loadingStore.start()
    }
    if (config.method === 'get') {
      config.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
      config.headers['Pragma'] = 'no-cache'
      config.headers['Expires'] = '0'
      if (!config.params) config.params = {}
      config.params._t = Date.now()
    }
    // JSON body 统一转换 camelCase → snake_case
    if (config.data
      && !(config.data instanceof FormData)
      && !(config.data instanceof Blob)
      && !(config.data instanceof File)) {
      try {
        config.data = toSnakeCase(config.data)
      } catch (e) {
        console.warn('[Axios] toSnakeCase 转换请求 body 失败:', e)
      }
    }
    // URL query params 统一转换 camelCase → snake_case
    if (config.params && typeof config.params === 'object') {
      try {
        config.params = toSnakeCase(config.params)
      } catch (e) {
        console.warn('[Axios] toSnakeCase 转换 query params 失败:', e)
      }
    }
    return config
  },
  (error) => {
    if (!error.config?.skipLoading) {
      const loadingStore = useLoadingStore()
      loadingStore.stop()
    }
    return Promise.reject(error)
  }
)

apiClient.interceptors.response.use(
  (response) => {
    if (!response.config.skipLoading) {
      const loadingStore = useLoadingStore()
      loadingStore.stop()
    }
    // 统一转换响应数据 snake_case → camelCase
    if (response.data) {
      try {
        const before = JSON.stringify(response.data).slice(0, 200)
        response.data = toCamelCase(response.data)
        const after = JSON.stringify(response.data).slice(0, 200)
        if (before !== after) {
          console.debug('[Axios] 响应转换:', response.config?.url, '\n  前:', before, '\n  后:', after)
        }
      } catch (e) {
        console.warn('[Axios] toCamelCase 转换响应数据失败:', e)
      }
    }
    return response
  },
  async (error) => {
    if (!error.config?.skipLoading) {
      const loadingStore = useLoadingStore()
      loadingStore.stop()
    }
    const originalRequest = error.config
    const userStore = useUserStore()

    if (error.response?.status === 401 && !originalRequest._retry) {
      // 登录/刷新接口自身的 401 直接透传，不做拦截重试
      if (originalRequest.url && originalRequest.url.includes('/users/login/')) {
        return Promise.reject(error)
      }

      // 无 token 时说明用户未登录，不需要刷新 token 或强制登出
      if (!userStore.token) {
        return Promise.reject(error)
      }

      originalRequest._retry = true

      // 统一由 userStore 刷新：并发 401 会复用同一 in-flight promise，
      // 刷新成功后各请求用新 token 重发；失败则触发幂等登出（仅一次）。
      const refreshed = await userStore.refreshAccessToken()
      if (refreshed) {
        originalRequest.headers.Authorization = `Bearer ${userStore.token}`
        return apiClient(originalRequest)
      }
      userStore.forceLogout('tokenExpired')
      return Promise.reject(error)
    }

    return Promise.reject(error)
  }
)

export { apiClient }
export default apiClient

export function updateBaseURL(newURL) {
  apiClient.defaults.baseURL = newURL
}
