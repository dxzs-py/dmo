/**
 * 子代理（SubAgentRuntime）API 模块
 *
 * 对接后端 /api/v1/ai-engine/subagents/ 端点：
 * - GET  /subagents/?parent_thread_id=xxx：查询父线程下的子代理列表（SubAgentCard 数据源）
 *
 * 子代理审批恢复走统一审批端点（/approvals/{interrupt_id}/resume/），
 * 与主 agent 审批完全同链路，不再提供独立的 subagents/resume 调用。
 */

import { apiClient } from '@/api/axios'

/**
 * 查询指定父线程下的子代理列表
 *
 * 后端返回 { code, message, data: { subagents: [...] } }，subagents 经 axios
 * 拦截器 toCamelCase 后为 camelCase：threadId / agentName / status /
 * pendingInterruptInfo / resultPreview / createdAt / assistantMessageId。
 *
 * @param {string} parentThreadId - 父线程 thread_id（主 agent = session_id 或 task_id）
 * @returns {Promise} axios response（response.data.data.subagents 为子代理列表）
 */
export function getSubagents(parentThreadId) {
  if (!parentThreadId) {
    return Promise.reject(new Error('parentThreadId 不能为空'))
  }
  return apiClient.get('/ai-engine/subagents/', {
    params: { parent_thread_id: parentThreadId },
  })
}
