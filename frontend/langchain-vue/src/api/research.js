import { apiClient } from './axios'
import settings from '../config/settings'
import { fetchSSE } from '@/utils/sse'

export const deepResearchAPI = {
  start(data) { return apiClient.post('/research/start/', data) },
  getStatus(taskId) { return apiClient.get(`/research/status/${taskId}/`) },
  getResults(taskId) { return apiClient.get(`/research/results/${taskId}/`) },
  getTasks(params = {}) { return apiClient.get('/research/tasks/', { params }) },
  deleteTask(taskId, params = {}) { return apiClient.delete(`/research/task/${taskId}/`, { params }) },
  getFiles(taskId) { return apiClient.get(`/research/${taskId}/files/`) },
  downloadFile(taskId, filename) { return `${settings.API_BASE_URL}/research/${taskId}/file/download/${filename}` },
  getFileContent(taskId, filename) { return apiClient.get(`/research/${taskId}/file/content/${filename}/`) },
  searchFiles(query) { return apiClient.get('/research/search/', { params: { keyword: query } }) },
  streamFetch(taskId, options = {}) {
    return fetchSSE(`/research/stream/${taskId}/`, {
      injectTokenQuery: true,
      ...options,
    })
  },
  continueResearch(taskId, data = {}) {
    return apiClient.post(`/research/${taskId}/continue/`, data)
  },
}
