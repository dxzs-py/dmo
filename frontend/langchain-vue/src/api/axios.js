import axios from 'axios'
import settings from '../config/settings'
import { useUserStore } from '@/stores/user'
import { useLoadingStore } from '@/stores/loading'

let isRefreshing = false
let refreshSubscribers = []

const apiClient = axios.create({
  baseURL: settings.API_BASE_URL,
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

function subscribeTokenRefresh(cb) {
  refreshSubscribers.push(cb)
}

function onTokenRefreshed(token) {
  refreshSubscribers.forEach(cb => cb(token))
  refreshSubscribers = []
}

apiClient.interceptors.response.use(
  (response) => {
    if (!response.config.skipLoading) {
      const loadingStore = useLoadingStore()
      loadingStore.stop()
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
      if (originalRequest.url && originalRequest.url.includes('/users/login/')) {
        return Promise.reject(error)
      }

      // 无 token 时说明用户未登录，不需要刷新 token 或强制登出
      if (!userStore.token) {
        return Promise.reject(error)
      }

      if (isRefreshing) {
        return new Promise((resolve) => {
          subscribeTokenRefresh((token) => {
            originalRequest.headers.Authorization = `Bearer ${token}`
            resolve(apiClient(originalRequest))
          })
        })
      }

      originalRequest._retry = true
      isRefreshing = true

      try {
        const refreshed = await userStore.refreshAccessToken()
        if (refreshed) {
          onTokenRefreshed(userStore.token)
          originalRequest.headers.Authorization = `Bearer ${userStore.token}`
          return apiClient(originalRequest)
        } else {
          userStore.forceLogout('token_expired')
        }
      } catch (refreshError) {
        userStore.forceLogout('token_invalid')
        return Promise.reject(refreshError)
      } finally {
        isRefreshing = false
      }
    }

    return Promise.reject(error)
  }
)

export { apiClient }
export default apiClient

export function updateBaseURL(newURL) {
  apiClient.defaults.baseURL = newURL
}
