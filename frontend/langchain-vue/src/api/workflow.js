import { apiClient } from './axios'
import settings from '../config/settings'
import { useUserStore } from '@/stores/user'
import { toSnakeCase } from '@/utils/sessionTransformers'

/** 组装 SSE 请求头（Authorization 注入） */
function buildSSEHeaders() {
  const userStore = useUserStore()
  const headers = { Accept: 'text/event-stream' }
  if (userStore.token) headers.Authorization = `Bearer ${userStore.token}`
  return headers
}

/**
 * 学习工作流 SSE 直连封装（绕过 fetchSSE）
 *
 * 背景：Vite dev server 的 http-proxy 对携带 AbortSignal 的 text/event-stream 响应
 * 会整体缓冲，流结束后才一次性转发，导致 workflow_step 中间事件无法实时到达
 * （步骤条只显示 start/最终态，中间节点不显示 active）。
 * 无 signal 时 proxy 直接透传，事件实时到达；中断控制交由 readSSEStream 的 signal
 * （abort 时 break + reader.releaseLock，后端生成器随之结束）。
 */
export const workflowAPI = {
  /** SSE 流式启动（无 signal 直连，避免 Vite proxy 缓冲，供 WorkflowView 使用） */
  startStreamRaw(data) {
    return fetch(`${settings.apiBaseUrl}/learning/start/stream/`, {
      method: 'POST',
      headers: { ...buildSSEHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify(toSnakeCase(data)),
    })
  },
  getState(threadId) { return apiClient.get(`/learning/status/${threadId}/`) },
  submitAnswers(threadId, data) { return apiClient.post('/learning/submit/', { threadId: threadId, answers: data }) },
  getTasks(params = {}) { return apiClient.get('/learning/tasks/', { params }) },
  getFiles(threadId) { return apiClient.get(`/learning/${threadId}/files/`) },
  downloadFile(threadId, filename) { return `${settings.apiBaseUrl}/learning/${threadId}/file/download/${filename}` },
  getFileContent(threadId, filename) { return apiClient.get(`/learning/${threadId}/file/content/${filename}/`) },
  deleteTask(threadId) { return apiClient.delete(`/learning/tasks/${threadId}/`) },
  /** 继续练习（无 signal 直连，避免 Vite proxy 缓冲，供 WorkflowView 使用） */
  restartStreamRaw(threadId) {
    return fetch(`${settings.apiBaseUrl}/learning/${threadId}/restart/stream/`, {
      method: 'POST',
      headers: buildSSEHeaders(),
    })
  },
  /** 跨轮次题目列表（支持 ?attempt_index= 过滤） */
  getQuestions(threadId, params = {}) { return apiClient.get(`/learning/${threadId}/questions/`, { params }) },
  /** 修改单题答案并重新评分 */
  updateQuestion(threadId, questionId, userAnswer) {
    return apiClient.put(`/learning/${threadId}/questions/${questionId}/`, { userAnswer })
  },
  /** 练习轮次历史 */
  getAttempts(threadId) { return apiClient.get(`/learning/${threadId}/attempts/`) },
  /** 监听已有线程进度（无 signal 直连，token 走 query 参数，供 WorkflowView 使用） */
  streamFetchRaw(threadId) {
    const userStore = useUserStore()
    const tokenQuery = userStore.token
      ? `?token=${encodeURIComponent(userStore.token)}`
      : ''
    return fetch(`${settings.apiBaseUrl}/learning/stream/${threadId}/${tokenQuery}`, {
      method: 'GET',
      headers: buildSSEHeaders(),
    })
  },
}
