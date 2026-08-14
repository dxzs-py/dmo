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
  downloadFile(taskId, filename) { return `${settings.apiBaseUrl}/research/${taskId}/file/download/${filename}` },
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
  /**
   * 单独重启失败子代理（Task 6）
   *
   * body 为前端 camelCase 键（agentPath / toolCallId），由 axios 请求拦截器
   * toSnakeCase 转换为后端契约键 agent_path / tool_call_id（网络协议边界）。
   *
   * @param {string} taskId - 深度研究任务 ID
   * @param {{ agentPath: string[], toolCallId: string }} data - 目标失败子代理的 agentPath 与触发工具调用 ID
   */
  retrySubagent(taskId, data) {
    return apiClient.post(`/research/task/${taskId}/retry-subagent/`, data)
  },
}

