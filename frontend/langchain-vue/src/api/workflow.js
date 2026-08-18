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
  /** 继续练习：创建新线程，复用学习计划与运行时配置，生成新一轮题目 */
  restart(threadId) { return apiClient.post(`/learning/${threadId}/restart/`) },
  /** 跨轮次题目列表（支持 ?attempt_index= 过滤） */
  getQuestions(threadId, params = {}) { return apiClient.get(`/learning/${threadId}/questions/`, { params }) },
  /** 修改单题答案并重新评分 */
  updateQuestion(threadId, questionId, userAnswer) {
    return apiClient.put(`/learning/${threadId}/questions/${questionId}/`, { userAnswer })
  },
  /** 练习轮次历史 */
  getAttempts(threadId) { return apiClient.get(`/learning/${threadId}/attempts/`) },
  streamFetch(threadId, options = {}) {
    return fetchSSE(`/learning/stream/${threadId}/`, {
      injectTokenQuery: true,
      ...options,
    })
  },
}
