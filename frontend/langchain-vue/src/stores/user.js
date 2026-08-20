import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import router from '@/router'
import { ElMessage } from 'element-plus'
import { userAPI } from '@/api/user'
import { logger } from '../utils/logger'
import { extractErrorMessage } from '../utils/apiErrorHandler'

const TOKEN_KEY = 'user_token'
const REFRESH_TOKEN_KEY = 'user_refresh_token'
const USER_INFO_KEY = 'user_info'

// 模块级 in-flight 单例：并发调用（axios 拦截器 / SSE / WebSocket 预检）共享，
// 避免用同一 refresh token 并发刷新被后端轮换机制（ROTATE_REFRESH_TOKENS）失效。
let refreshPromise = null

export const useUserStore = defineStore('user', () => {
  const token = ref(localStorage.getItem(TOKEN_KEY) || '')
  const refreshToken = ref(localStorage.getItem(REFRESH_TOKEN_KEY) || '')
  const userInfo = ref((() => { try { return JSON.parse(localStorage.getItem(USER_INFO_KEY) || '{}') } catch { return {} } })())
  const isLoggedIn = computed(() => !!token.value)
  const username = computed(() => userInfo.value?.username || '')
  const isAdmin = computed(() => !!userInfo.value?.isStaff)

  function setToken(newToken, newRefreshToken = null) {
    token.value = newToken
    localStorage.setItem(TOKEN_KEY, newToken)
    if (newRefreshToken) {
      refreshToken.value = newRefreshToken
      localStorage.setItem(REFRESH_TOKEN_KEY, newRefreshToken)
    }
  }

  function setUserInfo(info) {
    userInfo.value = info
    localStorage.setItem(USER_INFO_KEY, JSON.stringify(info))
  }

  function clearUser() {
    token.value = ''
    refreshToken.value = ''
    userInfo.value = {}
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(REFRESH_TOKEN_KEY)
    localStorage.removeItem(USER_INFO_KEY)
  }

  async function login(credentials) {
    try {
      const response = await userAPI.login(credentials)
      const data = response.data
      if (data.code === 200) {
        const userData = data.data || data
        setToken(userData.access, userData.refresh)
        setUserInfo({
          id: userData.id,
          username: userData.username,
          email: userData.email,
          isStaff: userData.isStaff
        })
        return { success: true, message: data.message }
      }
      return { success: false, message: data.message || '登录失败' }
    } catch (error) {
      logger.error('登录失败:', error)
      const resolved = extractErrorMessage(error)
      return {
        success: false,
        message: resolved || '登录失败，请检查网络连接'
      }
    }
  }

  async function register(userData) {
    try {
      const response = await userAPI.register(userData)
      const data = response.data
      if (response.status === 201 || data.code === 200) {
        return { success: true, message: data.message || '注册成功' }
      }
      return { success: false, message: data.message || '注册失败' }
    } catch (error) {
      logger.error('注册失败:', error)
      const resolved = extractErrorMessage(error)
      if (resolved) return { success: false, message: resolved }
      const errors = error.response?.data
      let message = '注册失败'
      if (typeof errors === 'object' && errors !== null && errors.data) {
        const firstField = Object.keys(errors.data)[0]
        message = Array.isArray(errors.data[firstField]) ? errors.data[firstField][0] : firstField
      } else if (typeof errors === 'object' && errors !== null) {
        const firstError = Object.values(errors)[0]
        message = Array.isArray(firstError) ? firstError[0] : firstError
      }
      return { success: false, message }
    }
  }

  async function refreshAccessToken() {
    if (!refreshToken.value) return false
    // 已有刷新在进行中：复用同一 promise，等待其完成（并发去重）
    if (refreshPromise) return refreshPromise

    refreshPromise = (async () => {
      try {
        const response = await userAPI.refreshToken(refreshToken.value)
        const data = response.data
        const access = data.data?.access || data.access
        const newRefresh = data.data?.refresh || data.refresh
        if (access) {
          setToken(access, newRefresh || undefined)
          return true
        }
        return false
      } catch (error) {
        logger.error('刷新 token 失败:', error)
        // 不清空本地凭证：会话失效登出由调用方统一触发 forceLogout（幂等），
        // 避免并发调用方互相清空对方状态。
        return false
      } finally {
        refreshPromise = null
      }
    })()

    return refreshPromise
  }

  async function getUserInfo() {
    if (!token.value) return null
    try {
      const response = await userAPI.getUserInfo()
      const data = response.data
      if (data.code === 200) {
        setUserInfo(data.data)
        return data.data
      }
      return null
    } catch (error) {
      logger.error('获取用户信息失败:', error)
      return null
    }
  }

  async function logout() {
    try {
      if (refreshToken.value) {
        await userAPI.logout(refreshToken.value)
        logger.log('[Security] Secure logout completed, server-side data cleared')
      }
    } catch (error) {
      logger.warn('[Security] Secure logout request failed:', error)
    } finally {
      clearUser()
    }
  }

  function forceLogout(reason = 'tokenExpired') {
    // 幂等：会话已被清理（如并发刷新失败时多个调用方同时触发）时，
    // 跳过重复弹窗与跳转，仅首次执行清理与跳转。
    const hadSession = !!token.value || !!refreshToken.value
    clearUser()
    if (!hadSession) return

    const reasonMessages = {
      tokenExpired: '登录已过期，请重新登录',
      tokenInvalid: '登录凭证无效，请重新登录',
      sessionExpired: '会话已过期，请重新登录',
    }
    const message = reasonMessages[reason] || '登录已过期，请重新登录'

    ElMessage.warning({
      message,
      duration: 3000,
      showClose: true,
    })
    const currentPath = router.currentRoute.value.fullPath
    const redirectPath = currentPath && currentPath !== '/login' ? currentPath : '/'
    router.push({
      path: '/login',
      query: { redirect: redirectPath, expired: '1' },
    })
  }

  return {
    token,
    refreshToken,
    userInfo,
    isLoggedIn,
    username,
    isAdmin,
    setToken,
    setUserInfo,
    clearUser,
    login,
    register,
    refreshAccessToken,
    getUserInfo,
    logout,
    forceLogout,
  }
})
