/**
 * 统一审批 API 模块
 *
 * 对接后端 /api/v1/approvals/ 端点：
 * - POST /approvals/{interrupt_id}/resume/：恢复审批（chat / deep_research / learning source 均走 SSE 流）
 * - POST /approvals/{interrupt_id}/reject/：拒绝审批
 * - GET /approvals/?source_id=...：查询审批历史
 * - GET /approvals/{interrupt_id}/：查询单条审批
 */

import { apiClient } from '@/api/axios'

/**
 * 查询审批历史
 *
 * 对接后端 GET /approvals/ 端点，支持按 source_id / source / state 组合过滤。
 * 审批记录是工具调用数据的唯一持久化来源（Approval 模型替代了原 ResearchTask.tool_calls 字段）。
 *
 * @param {string} sourceId - 来源 ID（session_id 或 task_id）
 * @param {Object} [options] - 额外过滤选项
 * @param {string} [options.source] - 审批来源过滤（'chat' | 'deep_research' | 'learning'）
 * @param {string} [options.state] - 审批状态过滤（'pending' | 'processing' | 'approved' | 'rejected' | 'timeout' | 'waiting'）
 * @returns {Promise} axios response
 */
export function getApprovalHistory(sourceId, options = {}) {
  if (!sourceId) {
    return Promise.reject(new Error('sourceId 不能为空'))
  }
  const params = { sourceId }
  if (options.source) params.source = options.source
  if (options.state) params.state = options.state
  return apiClient.get('/approvals/', { params })
}

/**
 * 查询单条审批
 *
 * @param {string} interruptId - 审批中断 ID
 * @returns {Promise} axios response
 */
export function getApprovalDetail(interruptId) {
  if (!interruptId) {
    return Promise.reject(new Error('interruptId 不能为空'))
  }
  return apiClient.get(`/approvals/${interruptId}/`)
}

/**
 * 恢复审批（执行与连接解耦后的普通 POST）。
 *
 * 后端 `ApprovalResumeView` 统一返回 JSON：
 * - `{ status: 'resumed' }`：恢复信令已发布，agent 由 FastAPI 执行服务恢复，
 *   后续输出经 WebSocket 同步（stream_* / tool_call_* / approval_* / stream_completed）
 * - `{ status: 'waiting_for_others', state: 'waiting' }`：批量审批等待
 * - `{ idempotent: true, state }`：审批已被处理（幂等响应）
 * - 错误响应：{ code, message }
 *
 * @param {string} interruptId - 审批中断 ID
 * @param {{ approved: boolean, user_input?: string, provider_id?: string|null, model_name?: string|null, [key: string]: any }} data - 审批数据
 * @param {{ signal?: AbortSignal }} [options] - 透传给 apiClient.post 的选项
 * @returns {Promise} axios response（response.data.data 为上述 JSON data）
 */
export function resumeApprovalStream(interruptId, data, options = {}) {
  if (!interruptId) {
    return Promise.reject(new Error('interruptId 不能为空'))
  }
  const url = `/approvals/${interruptId}/resume/`
  return apiClient.post(url, data, options)
}
