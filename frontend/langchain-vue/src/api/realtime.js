import { apiClient } from '@/api/axios'

/**
 * 实时同步快照 API
 *
 * 对接后端 /api/v1/realtime/snapshot/{session_id}/ 端点：
 * 关键事件（stream_finalized / tool_call_completed / approval_approved）触发后，
 * 前端通过本接口获取会话的最新快照，与本地状态对比并修复差异。
 */

/**
 * 获取指定会话的实时同步快照
 *
 * GET /api/v1/realtime/snapshot/{session_id}/
 * 返回该会话最新的消息列表、工具调用状态、审批状态等。
 * 使用 skipLoading 避免触发全局 loading 状态。
 *
 * @param {string} sessionId - 会话 ID
 * @returns {Promise} axios response - data 包含会话快照数据
 */
export function getSessionSnapshot(sessionId) {
  return apiClient.get(`/realtime/snapshot/${sessionId}/`, { skipLoading: true })
}
