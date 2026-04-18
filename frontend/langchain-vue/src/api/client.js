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
    const duration = response.headers['x-request-duration']
    if (duration) {
      const durationMs = parseFloat(duration)
      if (durationMs > 5) {
        console.log(`[API] ${response.config.method?.toUpperCase()} ${response.config.url} - ${durationMs.toFixed(3)}s`)
      }
      if (durationMs > 10) {
        console.warn(`[API] Slow response: ${response.config.url} took ${durationMs.toFixed(3)}s`)
      }
    }
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

function validateChatRequest(data) {
  const errors = []

  if (!data.message || typeof data.message !== 'string') {
    errors.push('消息内容不能为空')
  } else {
    const trimmedMessage = data.message.trim()
    if (trimmedMessage.length === 0) {
      errors.push('消息内容不能为空或仅包含空格')
    } else if (trimmedMessage.length > settings.API_VALIDATION.MESSAGE_MAX_LENGTH) {
      errors.push(`消息内容不能超过${settings.API_VALIDATION.MESSAGE_MAX_LENGTH}个字符`)
    }
  }

  if (data.mode && !settings.API_VALIDATION.ALLOWED_MODES.includes(data.mode)) {
    errors.push(`不支持的模式: ${data.mode}。允许的模式: ${settings.API_VALIDATION.ALLOWED_MODES.join(', ')}`)
  }

  if (data.session_id && !settings.API_VALIDATION.SESSION_ID_PATTERN.test(data.session_id)) {
    errors.push('会话ID格式无效')
  }

  if (data.chat_history && Array.isArray(data.chat_history)) {
    if (data.chat_history.length > settings.API_VALIDATION.CHAT_HISTORY_MAX_ITEMS) {
      errors.push(`聊天历史记录不能超过${settings.API_VALIDATION.CHAT_HISTORY_MAX_ITEMS}条`)
    }
    data.chat_history.forEach((msg, index) => {
      if (!['user', 'assistant', 'system'].includes(msg.role)) {
        errors.push(`聊天历史第${index + 1}条消息的角色无效`)
      }
      if (msg.content && msg.content.length > 50000) {
        errors.push(`聊天历史第${index + 1}条消息内容过长`)
      }
    })
  }

  return {
    valid: errors.length === 0,
    errors,
    sanitizedData: {
      ...data,
      message: data.message ? data.message.trim() : data.message,
    }
  }
}

export const chatAPI = {
  sendMessage(data) {
    const validation = validateChatRequest(data)
    if (!validation.valid) {
      return Promise.reject(new Error(validation.errors.join('; ')))
    }
    return apiClient.post('/chat/', validation.sanitizedData)
  },

  streamMessage(data, options = {}) {
    const validation = validateChatRequest(data)
    if (!validation.valid) {
      return Promise.reject(new Error(validation.errors.join('; ')))
    }

    const userStore = useUserStore()
    const headers = {
      'Content-Type': 'application/json',
    }
    if (userStore.token) {
      headers['Authorization'] = `Bearer ${userStore.token}`
    }
    return fetch(`${settings.API_BASE_URL}/chat/stream/`, {
      method: 'POST',
      mode: 'cors',
      credentials: 'include',
      headers,
      body: JSON.stringify(validation.sanitizedData),
      signal: options.signal,
    })
  },

  getModes() {
    return apiClient.get('/chat/modes/')
  },

  getSessions(params = {}) {
    const validatedParams = { ...params }
    if (validatedParams.page_size && validatedParams.page_size > 100) {
      validatedParams.page_size = 100
    }
    return apiClient.get('/chat/sessions/', { params: validatedParams })
  },

  createSession(data) {
    const sessionData = { ...data }
    if (sessionData.title && sessionData.title.length > 200) {
      sessionData.title = sessionData.title.slice(0, 200)
    }
    if (sessionData.mode && !settings.API_VALIDATION.ALLOWED_MODES.includes(sessionData.mode)) {
      sessionData.mode = 'basic-agent'
    }
    return apiClient.post('/chat/sessions/create/', sessionData)
  },

  getSession(sessionId) {
    if (!sessionId || !settings.API_VALIDATION.SESSION_ID_PATTERN.test(sessionId)) {
      return Promise.reject(new Error('会话ID格式无效'))
    }
    return apiClient.get(`/chat/sessions/${sessionId}/`)
  },

  updateSession(sessionId, data) {
    if (!sessionId) {
      return Promise.reject(new Error('会话ID不能为空'))
    }
    const updateData = {}
    if (data.title !== undefined) {
      if (typeof data.title !== 'string' || data.title.trim().length === 0) {
        return Promise.reject(new Error('标题不能为空'))
      }
      if (data.title.length > 200) {
        updateData.title = data.title.slice(0, 200)
      } else {
        updateData.title = data.title.trim()
      }
    }
    return apiClient.put(`/chat/sessions/${sessionId}/`, updateData)
  },

  deleteSession(sessionId) {
    if (!sessionId) {
      return Promise.reject(new Error('会话ID不能为空'))
    }
    return apiClient.delete(`/chat/sessions/${sessionId}/`)
  },

  addMessage(sessionId, data) {
    if (!sessionId) {
      return Promise.reject(new Error('会话ID不能为空'))
    }
    const messageData = { ...data }
    if (messageData.content && messageData.content.length > 50000) {
      messageData.content = messageData.content.slice(0, 50000)
    }
    if (!['user', 'assistant', 'system'].includes(messageData.role)) {
      messageData.role = 'user'
    }
    return apiClient.post(`/chat/sessions/${sessionId}/messages/`, messageData)
  },

  addMessagesBatch(sessionId, data) {
    if (!sessionId) {
      return Promise.reject(new Error('会话ID不能为空'))
    }
    if (!Array.isArray(data.messages) || data.messages.length === 0) {
      return Promise.reject(new Error('messages必须是非空数组'))
    }
    if (data.messages.length > settings.API_VALIDATION.BATCH_CREATE_MAX_ITEMS) {
      return Promise.reject(new Error(`批量创建数量不能超过${settings.API_VALIDATION.BATCH_CREATE_MAX_ITEMS}条`))
    }
    return apiClient.post(`/chat/sessions/${sessionId}/messages/batch/`, data)
  },

  updateMessage(messageId, data) {
    if (!messageId) {
      return Promise.reject(new Error('消息ID不能为空'))
    }
    const updateData = { ...data }
    if (updateData.content && updateData.content.length > 50000) {
      updateData.content = updateData.content.slice(0, 50000)
    }
    return apiClient.put(`/chat/messages/${messageId}/`, updateData)
  },

  uploadAttachment(sessionId, file) {
    if (!sessionId) {
      return Promise.reject(new Error('会话ID不能为空'))
    }
    if (!file) {
      return Promise.reject(new Error('请选择要上传的文件'))
    }
    const formData = new FormData()
    formData.append('file', file)
    return apiClient.post(`/chat/sessions/${sessionId}/attachments/`, formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
    })
  },

  getAttachments(sessionId) {
    if (!sessionId) {
      return Promise.reject(new Error('会话ID不能为空'))
    }
    return apiClient.get(`/chat/sessions/${sessionId}/attachments/list/`)
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

  uploadDocument(indexName, file) {
    const formData = new FormData()
    formData.append('file', file)
    return apiClient.post(`/rag/index/${indexName}/upload/`, formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
    })
  },
}

export const workflowAPI = {
  start(data) {
    return apiClient.post('/workflow/start/', data)
  },

  startStreamUrl() {
    return `${settings.API_BASE_URL}/workflow/start/stream/`
  },

  streamUrl(threadId) {
    return `${settings.API_BASE_URL}/workflow/stream/${threadId}/`
  },

  getState(threadId) {
    return apiClient.get(`/workflow/status/${threadId}/`)
  },

  submitAnswers(threadId, data) {
    return apiClient.post('/workflow/submit/', { thread_id: threadId, answers: data })
  },

  getTasks(params = {}) {
    return apiClient.get('/workflow/tasks/', { params })
  },

  getFiles(threadId) {
    return apiClient.get(`/workflow/${threadId}/files/`)
  },

  downloadFile(threadId, filename) {
    return `${settings.API_BASE_URL}/workflow/${threadId}/file/download/${filename}`
  },

  getFileContent(threadId, filename) {
    return apiClient.get(`/workflow/${threadId}/file/content/${filename}/`)
  },

  deleteTask(threadId) {
    return apiClient.delete(`/workflow/task/${threadId}/`)
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

  getTasks(params = {}) {
    return apiClient.get('/deep-research/tasks/', { params })
  },

  getFiles(taskId) {
    return apiClient.get(`/deep-research/${taskId}/files/`)
  },

  downloadFile(taskId, filename) {
    return `${settings.API_BASE_URL}/deep-research/${taskId}/file/download/${filename}`
  },

  getFileContent(taskId, filename) {
    return apiClient.get(`/deep-research/${taskId}/file/content/${filename}/`)
  },

  deleteTask(taskId) {
    return apiClient.delete(`/deep-research/task/${taskId}/`)
  },

  streamUrl(taskId) {
    return `${settings.API_BASE_URL}/deep-research/stream/${taskId}/`
  },

  searchFiles(params = {}) {
    return apiClient.get('/deep-research/search/', { params })
  },
}

export const healthAPI = {
  check() {
    return apiClient.get('/health/')
  },
}

export async function* streamChat(request) {
  const validation = validateChatRequest(request)
  if (!validation.valid) {
    throw new Error(validation.errors.join('; '))
  }

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
      body: JSON.stringify(validation.sanitizedData || request),
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
