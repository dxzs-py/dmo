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
    return apiClient.post('/chat/', data)
  },
  streamMessage(data) {
    return fetch(`${settings.API_BASE_URL}/chat/stream/`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(data),
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

export default apiClient
