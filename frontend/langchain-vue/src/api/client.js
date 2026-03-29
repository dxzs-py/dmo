import axios from 'axios'
import settings from '../settings'

const apiClient = axios.create({
  baseURL: settings.API_BASE_URL,
  timeout: 120000,
  headers: {
    'Content-Type': 'application/json',
  },
})

apiClient.interceptors.request.use(
  (config) => {
    console.log(`[API] ${config.method?.toUpperCase()} ${config.url}`)
    return config
  },
  (error) => {
    console.error('[API] Request error:', error)
    return Promise.reject(error)
  }
)

apiClient.interceptors.response.use(
  (response) => {
    return response
  },
  (error) => {
    console.error('[API] Response error:', error)
    return Promise.reject(error)
  }
)

export const chatAPI = {
  sendMessage(data) {
    return apiClient.post('/chat/', data)
  },

  streamMessage(data, options = {}) {
    return fetch(`${settings.API_BASE_URL}/chat/stream/`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(data),
      signal: options.signal,
    })
  },

  getModes() {
    return apiClient.get('/chat/modes/')
  },
}

export const ragAPI = {
  query(data) {
    return apiClient.post('/rag/query/', data)
  },

  getIndexes() {
    return apiClient.get('/rag/indexes/')
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
    return apiClient.post(`/workflow/submit/`, { thread_id: threadId, ...data })
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

  let response

  try {
    response = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
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
