import axios from 'axios'
import settings from '../settings'
import { useUserStore } from '@/stores/user'

let isRefreshing = false
let refreshSubscribers = []

const apiClient = axios.create({
  baseURL: settings.API_BASE_URL,
  timeout: 300000,
  headers: {
    'Content-Type': 'application/json',
  },
})

apiClient.interceptors.request.use(
  (config) => {
    console.log(`[API] ${config.method?.toUpperCase()} ${config.url}`)
    const userStore = useUserStore()
    if (userStore.token) {
      config.headers.Authorization = `Bearer ${userStore.token}`
    }
    return config
  },
  (error) => {
    console.error('[API] Request error:', error)
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
    return response
  },
  async (error) => {
    console.error('[API] Response error:', error)
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

export const chatAPI = {
  sendMessage(data) {
    return apiClient.post('/chat/', data)
  },

  streamMessage(data, options = {}) {
    const userStore = useUserStore()
    const headers = {
      'Content-Type': 'application/json',
    }
    if (userStore.token) {
      headers['Authorization'] = `Bearer ${userStore.token}`
    }
    return fetch(`${settings.API_BASE_URL}/chat/stream/`, {
      method: 'POST',
      headers,
      body: JSON.stringify(data),
      signal: options.signal,
    })
  },

  getModes() {
    return apiClient.get('/chat/modes/')
  },

  getSessions() {
    return apiClient.get('/chat/sessions/')
  },

  createSession(data) {
    return apiClient.post('/chat/sessions/create/', data)
  },

  getSession(sessionId) {
    return apiClient.get(`/chat/sessions/${sessionId}/`)
  },

  updateSession(sessionId, data) {
    return apiClient.put(`/chat/sessions/${sessionId}/`, data)
  },

  deleteSession(sessionId) {
    return apiClient.delete(`/chat/sessions/${sessionId}/`)
  },

  addMessage(sessionId, data) {
    return apiClient.post(`/chat/sessions/${sessionId}/messages/`, data)
  },

  addMessagesBatch(sessionId, data) {
    return apiClient.post(`/chat/sessions/${sessionId}/messages/batch/`, data)
  },

  updateMessage(messageId, data) {
    return apiClient.put(`/chat/messages/${messageId}/`, data)
  },
}

export const ragAPI = {
  query(data) {
    return apiClient.post('/rag/query/', data)
  },

  getIndexes() {
    return apiClient.get('/rag/index/list/')
  },

  buildIndex(data) {
    return apiClient.post('/rag/index/', data)
  },
}

export const workflowAPI = {
  start(data) {
    return apiClient.post('/workflow/start/', data)
  },

  getState(threadId) {
    return apiClient.get(`/workflow/status/${threadId}/`)
  },

  submitAnswers(threadId, data) {
    return apiClient.post('/workflow/submit/', { thread_id: threadId, answers: data })
  },
}

export const deepResearchAPI = {
  start(data) {
    return apiClient.post('/deep-research/start/', data)
  },

  getStatus(taskId) {
    return apiClient.get(`/deep-research/status/${taskId}/`)
  },

  getResult(taskId) {
    return apiClient.get(`/deep-research/result/${taskId}/`)
  },
}

export const healthAPI = {
  check() {
    return apiClient.get('/health/')
  },
}

export async function* streamChat(request) {
  const url = `${settings.API_BASE_URL}/chat/stream/`

  const userStore = useUserStore()
  const headers = {
    'Content-Type': 'application/json',
  }
  if (userStore.token) {
    headers['Authorization'] = `Bearer ${userStore.token}`
  }

  let response

  try {
    response = await fetch(url, {
      method: 'POST',
      headers,
      body: JSON.stringify(request),
    })

    if (!response.ok) {
      const errorText = await response.text()
      throw new Error(`HTTP ${response.status}: ${errorText}`)
    }

    if (!response.body) {
      throw new Error('Response body is null')
    }
  } catch (error) {
    console.error('Failed to initiate chat stream:', error)
    throw error
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      const { done, value } = await reader.read()

      if (done) {
        break
      }

      buffer += decoder.decode(value, { stream: true })

      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const dataStr = line.slice(6)

          if (dataStr.trim()) {
            try {
              const data = JSON.parse(dataStr)
              yield data
            } catch {
              console.warn('Failed to parse SSE data:', dataStr)
            }
          }
        }
      }
    }

    if (buffer.trim()) {
      if (buffer.startsWith('data: ')) {
        const dataStr = buffer.slice(6)
        if (dataStr.trim() && dataStr !== '[DONE]') {
          try {
            const data = JSON.parse(dataStr)
            yield data
          } catch {
            console.warn('Failed to parse remaining buffer:', dataStr)
          }
        }
      }
    }
  } catch (error) {
    console.error('Error reading stream:', error)
    throw error
  } finally {
    reader.releaseLock()
  }
}

export default apiClient
