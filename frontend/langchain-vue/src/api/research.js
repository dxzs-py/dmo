import { apiClient } from './axios'
import settings from '../config/settings'
import { fetchSSE } from '@/utils/sse'

export const deepResearchAPI = {
  start(data) { return apiClient.post('/research/start/', data) },
  getStatus(taskId) { return apiClient.get(`/research/status/${taskId}/`) },
  getTasks(params = {}) { return apiClient.get('/research/tasks/', { params }) },
  deleteTask(taskId, params = {}) { return apiClient.delete(`/research/tasks/${taskId}/`, { params }) },
  getFiles(taskId) { return apiClient.get(`/research/${taskId}/files/`) },
  downloadFile(taskId, filename) { return `${settings.apiBaseUrl}/research/${taskId}/file/download/${filename}` },
  getFileContent(taskId, filename) { return apiClient.get(`/research/${taskId}/file/content/${filename}/`) },
  searchFiles(query) { return apiClient.get('/research/search/', { params: { keyword: query } }) },
  streamFetch(taskId, options = {}) {
    // 不再注入 ?token=：fetchSSE 已带 Authorization 头（见 utils/sse.js buildHeaders），
    // 查询串传令牌会让 JWT 进入浏览器历史 / Referer / 各类访问日志。
    return fetchSSE(`/research/stream/${taskId}/`, { ...options })
  },
  continueResearch(taskId, data = {}) {
    return apiClient.post(`/research/${taskId}/continue/`, data)
  },
}

