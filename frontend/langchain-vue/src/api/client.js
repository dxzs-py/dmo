import axios from 'axios'
import settings from '../settings'

const apiClient = axios.create({
  baseURL: settings.API_BASE_URL,
  timeout: 60000,
  headers: {
    'Content-Type': 'application/json',
  },
})

export const chatAPI = {
  sendMessage(data) {
    return apiClient.post('/api/chat/', data)
  },
  streamMessage(data) {
    return fetch(`${settings.API_BASE_URL}/api/chat/stream/`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(data),
    })
  },
  getModes() {
    return apiClient.get('/api/chat/modes/')
  },
}

export const ragAPI = {
  query(data) {
    return apiClient.post('/api/rag/query/', data)
  },
}

export const workflowAPI = {
  start(data) {
    return apiClient.post('/api/workflow/start/', data)
  },
  getState(threadId) {
    return apiClient.get(`/api/workflow/status/${threadId}/`)
  },
  submitAnswers(threadId, data) {
    return apiClient.post(`/api/workflow/submit/`, { thread_id: threadId, ...data })
  },
}

export const deepResearchAPI = {
  start(data) {
    return apiClient.post('/api/deep-research/', data)
  },
}

export const healthAPI = {
  check() {
    return apiClient.get('/health/')
  },
}

export default apiClient
