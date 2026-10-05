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
 * 学习工作流 raw SSE 统一入口（绕过 fetchSSE）
 *
 * 职责边界：只负责"发起请求 + 归一请求头 + 返回响应对象"，不读流——
 * 流读取归调用方（useSSEStream），与 useSSEStream 的边界不变。
 * 归一内容：
 * - 请求头：buildSSEHeaders（Accept + Authorization 注入）；有 body 时追加
 *   Content-Type: application/json 并序列化（camelCase → snake_case）
 * - credentials：维持 fetch 默认 same-origin（与原三处一致，无跨域凭据需求）
 * - signal：一律不透传，错误不做捕获归一，fetch 拒绝原样向上传播（与原三处一致）
 *
 * 背景：Vite dev server 的 http-proxy 对携带 AbortSignal 的 text/event-stream 响应
 * 会整体缓冲，流结束后才一次性转发，导致 workflow_step 中间事件无法实时到达
 * （步骤条只显示 start/最终态，中间节点不显示 active）。
 * 无 signal 时 proxy 直接透传，事件实时到达；中断控制交由 readSSEStream 的 signal
 * （abort 时 break + reader.releaseLock，后端生成器随之结束）。
 */
function rawSSEFetch(url, { method = 'GET', body } = {}) {
  const headers = buildSSEHeaders()
  const options = { method, headers }
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    options.body = JSON.stringify(toSnakeCase(body))
  }
  return fetch(url, options)
}

export const workflowAPI = {
  /** SSE 流式启动（无 signal 直连，避免 Vite proxy 缓冲，供 WorkflowView 使用） */
  startStreamRaw(data) {
    return rawSSEFetch(`${settings.apiBaseUrl}/learning/start/stream/`, {
      method: 'POST',
      body: data,
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
    return rawSSEFetch(`${settings.apiBaseUrl}/learning/${threadId}/restart/stream/`, {
      method: 'POST',
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
  /** 监听已有线程进度（无 signal 直连，供 WorkflowView 使用） */
  streamFetchRaw(threadId) {
    // 令牌走 Authorization 头（rawSSEFetch 已注入），不再用 ?token= 查询参数。
    return rawSSEFetch(`${settings.apiBaseUrl}/learning/stream/${threadId}/`, {
      method: 'GET',
    })
  },
}
