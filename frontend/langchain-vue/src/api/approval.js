import { apiClient } from '@/api/axios'
import { fetchSSE } from '@/utils/sse'

/**
 * 统一审批 API 模块
 *
 * 对接后端 /api/v1/approvals/ 端点：
 * - POST /approvals/{interrupt_id}/resume/：恢复审批
 *   - chat source：返回 SSE 流（agent 恢复执行的流式输出）
 *   - deep_research source：返回 202 Accepted（由 Celery 任务异步恢复）
 * - POST /approvals/{interrupt_id}/reject/：拒绝审批
 * - GET /approvals/?source_id=...：查询审批历史
 * - GET /approvals/{interrupt_id}/：查询单条审批
 */

/**
 * 恢复审批（非流式，用于 deep_research source）
 *
 * @deprecated 自后端重构后，深度研究审批已通过 POST /chat/approval/ 流式执行，
 *             与代理模式统一。请使用 resumeApprovalStream 替代。
 *
 * deep_research source 的恢复由 Celery 任务异步执行，
 * 后端返回 202 Accepted，前端通过 SSE/轮询获取后续状态。
 *
 * @param {string} interruptId - 审批中断 ID
 * @param {{ approved: boolean, user_input?: string }} data - 审批数据
 * @returns {Promise} axios response
 */
export function resumeApproval(interruptId, data) {
  if (!interruptId) {
    return Promise.reject(new Error('interruptId 不能为空'))
  }
  return apiClient.post(`/approvals/${interruptId}/resume/`, data)
}

/**
 * 拒绝审批（统一端点，适用于 chat 和 deep_research source）
 *
 * @deprecated 自后端重构后，深度研究审批已通过 POST /chat/approval/ 流式执行，
 *             与代理模式统一。请使用 resumeApprovalStream 替代。
 *
 * @param {string} interruptId - 审批中断 ID
 * @returns {Promise} axios response
 */
export function rejectApproval(interruptId) {
  if (!interruptId) {
    return Promise.reject(new Error('interruptId 不能为空'))
  }
  return apiClient.post(`/approvals/${interruptId}/reject/`)
}

/**
 * 查询审批历史
 *
 * @param {string} sourceId - 来源 ID（session_id 或 task_id）
 * @returns {Promise} axios response
 */
export function getApprovalHistory(sourceId) {
  if (!sourceId) {
    return Promise.reject(new Error('sourceId 不能为空'))
  }
  return apiClient.get('/approvals/', { params: { source_id: sourceId } })
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
 * 查询审批当前状态
 *
 * @deprecated 自后端重构后，深度研究审批已通过 POST /chat/approval/ 流式执行，
 *             不再需要独立的 state 轮询端点。
 *
 * GET /api/v1/approvals/{interrupt_id}/state/
 * 返回审批的最新状态（state、resume_value、created_at 等），
 * 供前端在收到幂等响应时同步本地审批状态。
 *
 * @param {string} interruptId - 审批中断 ID
 * @returns {Promise} axios response - data 包含 { state, interrupt_id, source, ... }
 */
export function getApprovalState(interruptId) {
  if (!interruptId) {
    return Promise.reject(new Error('interruptId 不能为空'))
  }
  return apiClient.get(`/approvals/${interruptId}/state/`)
}

/**
 * 恢复审批（SSE 流式，用于 chat source）
 *
 * chat source 的恢复会触发 agent 继续执行，后端返回 SSE 流。
 * 使用 fetchSSE 获得：
 * - 自动 token 注入与 401 刷新
 * - 服务器错误（5xx）自动重试
 * - 连接去重与 AbortSignal 支持
 *
 * 返回 fetch Response 对象，调用方需使用 readSSEStream 消费流。
 *
 * @param {string} interruptId - 审批中断 ID
 * @param {{ approved: boolean, user_input?: string }} data - 审批数据
 * @param {{ signal?: AbortSignal, headers?: Object }} [options] - 额外选项
 * @returns {Promise<Response>} fetch Response 对象
 */
export async function resumeApprovalStream(interruptId, data, options = {}) {
  if (!interruptId) {
    throw new Error('interruptId 不能为空')
  }
  // 统一使用 /approvals/{interruptId}/resume/ SSE 流式端点（chat 和 deep_research 通用）
  const url = `/approvals/${interruptId}/resume/`
  return fetchSSE(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
    signal: options.signal,
    ...(options.headers ? { headers: options.headers } : {}),
  })
}
