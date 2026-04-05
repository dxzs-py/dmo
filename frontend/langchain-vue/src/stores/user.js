import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import apiClient from '@/api/client'

const TOKEN_KEY = 'user_token'
const REFRESH_TOKEN_KEY = 'user_refresh_token'
const USER_INFO_KEY = 'user_info'

const ERROR_CODE_MESSAGES = {
  10001: '认证失败，请重新登录',
  10002: 'Token已过期，请重新登录',
  10003: '无效的Token，请重新登录',
  20001: '数据验证失败，请检查输入',
  20003: '请求的资源不存在',
  30001: '权限不足，无法执行此操作',
  30002: '请求过于频繁，请稍后再试',
  50001: '服务器内部错误，请稍后重试',
  50002: '服务器繁忙，请稍后重试',
}

function resolveErrorMessage(error) {
  if (!error.response?.data) return null
  const data = error.response.data
  if (data.code && ERROR_CODE_MESSAGES[data.code]) {
    return ERROR_CODE_MESSAGES[data.code]
  }
  if (data.message) return data.message
  if (data.detail) return data.detail
  return null
}

export const useUserStore = defineStore('user', () => {
  const token = ref(localStorage.getItem(TOKEN_KEY) || '')
  const refreshToken = ref(localStorage.getItem(REFRESH_TOKEN_KEY) || '')
  const userInfo = ref(JSON.parse(localStorage.getItem(USER_INFO_KEY) || '{}'))
  const isLoggedIn = computed(() => !!token.value)
  const isAuthenticated = isLoggedIn
  const username = computed(() => userInfo.value?.username || '')

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
      const response = await apiClient.post('/users/login/', credentials)
      const data = response.data
      if (data.code === 200) {
        const userData = data.data || data
        setToken(userData.access, userData.refresh)
        setUserInfo({
          id: userData.id,
          username: userData.username,
          email: userData.email
        })
        return { success: true, message: data.message }
      }
      return { success: false, message: data.message || '登录失败' }
    } catch (error) {
      console.error('登录失败:', error)
      const resolved = resolveErrorMessage(error)
      return {
        success: false,
        message: resolved || '登录失败，请检查网络连接'
      }
    }
  }

  async function register(userData) {
    try {
      const response = await apiClient.post('/users/register/', userData)
      const data = response.data
      if (response.status === 201 || data.code === 200) {
        return { success: true, message: data.message || '注册成功' }
      }
      return { success: false, message: data.message || '注册失败' }
    } catch (error) {
      console.error('注册失败:', error)
      const resolved = resolveErrorMessage(error)
      if (resolved) return { success: false, message: resolved }
      const errors = error.response?.data
      let message = '注册失败'
      if (typeof errors === 'object' && errors !== null && errors.details) {
        const firstField = Object.keys(errors.details)[0]
        message = Array.isArray(errors.details[firstField]) ? errors.details[firstField][0] : firstField
      } else if (typeof errors === 'object' && errors !== null) {
        const firstError = Object.values(errors)[0]
        message = Array.isArray(firstError) ? firstError[0] : firstError
      }
      return { success: false, message }
    }
  }

  async function refreshAccessToken() {
    if (!refreshToken.value) return false
    try {
      const response = await apiClient.post('/users/login/refresh/', {
        refresh: refreshToken.value
      })
      const data = response.data
      if (data.access) {
        setToken(data.access)
        return true
      }
      return false
    } catch (error) {
      console.error('刷新 token 失败:', error)
      clearUser()
      return false
    }
  }

  async function getUserInfo() {
    if (!token.value) return null
    try {
      const response = await apiClient.get('/users/info/')
      const data = response.data
      if (data.code === 200) {
        setUserInfo(data.data)
        return data.data
      }
      return null
    } catch (error) {
      console.error('获取用户信息失败:', error)
      return null
    }
  }

  async function logout() {
    try {
      if (refreshToken.value) {
        await apiClient.post('/users/secure-logout/', {
          refresh: refreshToken.value
        })
        console.log('[Security] Secure logout completed, server-side data cleared')
      }
    } catch (error) {
      console.warn('[Security] Secure logout request failed:', error)
    } finally {
      clearUser()
    }
  }

  return {
    token,
    refreshToken,
    userInfo,
    isLoggedIn,
    isAuthenticated,
    username,
    setToken,
    setUserInfo,
    clearUser,
    login,
    register,
    refreshAccessToken,
    getUserInfo,
    logout
  }
})
