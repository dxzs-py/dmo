import { apiClient } from './axios'
import settings from '../config/settings'
import { fetchSSE } from '@/utils/sse'

export const workflowAPI = {
  start(data) { return apiClient.post('/learning/start/', data) },
  startStreamUrl() { return `${settings.apiBaseUrl}/learning/start/stream/` },
  streamUrl(threadId) { return `${settings.apiBaseUrl}/learning/stream/${threadId}/` },
  getState(threadId) { return apiClient.get(`/learning/status/${threadId}/`) },
  submitAnswers(threadId, data) { return apiClient.post('/learning/submit/', { threadId: threadId, answers: data }) },
  getTasks(params = {}) { return apiClient.get('/learning/tasks/', { params }) },
  getFiles(threadId) { return apiClient.get(`/learning/${threadId}/files/`) },
  downloadFile(threadId, filename) { return `${settings.apiBaseUrl}/learning/${threadId}/file/download/${filename}` },
  getFileContent(threadId, filename) { return apiClient.get(`/learning/${threadId}/file/content/${filename}/`) },
  deleteTask(threadId) { return apiClient.delete(`/learning/task/${threadId}/`) },
  streamFetch(threadId, options = {}) {
    return fetchSSE(`/learning/stream/${threadId}/`, {
      injectTokenQuery: true,
      ...options,
    })
  },
}
