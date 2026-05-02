import axios from 'axios'
import settings from '../config/settings'
import { useUserStore } from '@/stores/user'

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
    // 防止浏览器缓存 GET 请求
    if (config.method === 'get') {
      config.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
      config.headers['Pragma'] = 'no-cache'
      config.headers['Expires'] = '0'
      // 添加时间戳作为查询参数，强制每次请求都获取新数据
      if (!config.params) config.params = {}
      config.params._t = Date.now()
    }
    return config
  },
  (error) => Promise.reject(error)
)

function subscribeTokenRefresh(cb) {
  refreshSubscribers.push(cb)
}

function onTokenRefreshed(token) {
  refreshSubscribers.forEach(cb => cb(token))
  refreshSubscribers = []
}

apiClient.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config
    const userStore = useUserStore()

    if (error.response?.status === 401 && !originalRequest._retry) {
      if (originalRequest.url && originalRequest.url.includes('/users/login/')) {
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
          userStore.logout()
          window.location.href = '/login'
        }
      } catch (refreshError) {
        userStore.logout()
        window.location.href = '/login'
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
