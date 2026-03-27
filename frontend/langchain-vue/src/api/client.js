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
    return `${settings.API_BASE_URL}/chat/stream/`
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
  getDocuments(indexId) {
    return apiClient.get(`/rag/indexes/${indexId}/documents/`)
  },
}

export const workflowAPI = {
  start(data) {
    return apiClient.post('/workflow/start/', data)
  },
  getStatus(threadId) {
    return apiClient.get(`/workflow/status/${threadId}/`)
  },
}

export const deepResearchAPI = {
  start(data) {
    return apiClient.post('/deep-research/start/', data)
  },
  getStatus(taskId) {
    return apiClient.get(`/deep-research/status/${taskId}/`)
  },
}

export default apiClient
