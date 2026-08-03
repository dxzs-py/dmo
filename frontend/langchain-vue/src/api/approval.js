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
import { fetchSSE } from '@/utils/sse'

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
 * 恢复审批（SSE 流式，统一入口）
 *
 * 后端 `ApprovalResumeView`：
 * - chat / deep_research / learning source：返回 SSE 流（agent 恢复执行的流式输出）
 * - 批量审批等待（waiting_for_others）：返回 JSON（content-type: application/json）
 * - 幂等响应（已处理审批）：返回 JSON
 * - 错误响应：返回 JSON
 *
 * 前端使用 `fetchSSE` 发起请求，发送 `Accept: text/event-stream`。
 * 后端通过 `renderer_classes = [JSONRenderer, SSERenderer]` 实现 renderer 切换：
 * 非 SSE 场景由后端显式选择 JSONRenderer，确保 content-type 正确。
 *
 * 返回 fetch Response 对象，调用方需使用 `readSSEStream` 消费流，
 * 或先检查 `response.headers.get('content-type')` 区分 SSE / JSON。
 *
 * @param {string} interruptId - 审批中断 ID
 * @param {{ approved: boolean, user_input?: string, provider_id?: string|null, model_name?: string|null, [key: string]: any }} data - 审批数据
 *   - `approved` / `user_input` 由后端 `ApprovalWriteSerializer` 校验
 *   - 其余字段（provider_id / model_name / use_tools 等）由后端
 *     `_stream_chat_resume_generator` 通过 `request.data` 读取
 *   - `interrupt_id` / `session_id` 由后端从 URL path 与 Approval 模型自动获取，前端无需提交
 * @param {{ signal?: AbortSignal, headers?: Object }} [options] - 额外选项
 * @returns {Promise<Response>} fetch Response 对象
 */
export async function resumeApprovalStream(interruptId, data, options = {}) {
  if (!interruptId) {
    throw new Error('interruptId 不能为空')
  }
  // 统一使用 /approvals/{interruptId}/resume/ SSE 流式端点
  const url = `/approvals/${interruptId}/resume/`
  return fetchSSE(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
    signal: options.signal,
    ...(options.headers ? { headers: options.headers } : {}),
  })
}
