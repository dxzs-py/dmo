/**
 * 子代理（SubAgentRuntime）API 模块
 *
 * 对接后端 /api/v1/ai-engine/subagents/ 端点：
 * - GET  /subagents/?parent_thread_id=xxx[&recursive=true]：查询父线程下的
 *   子代理列表（SubAgentCard 数据源）；recursive=true 时服务端 BFS 一次
 *   请求返回全树（含任意深度嵌套后代）
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
 * @param {boolean} [recursive] - 为 true 时携带 recursive=true 查询参数，
 *   服务端 BFS 返回全树（含嵌套后代），单请求替代客户端逐层递归拉取
 * @returns {Promise} axios response（response.data.data.subagents 为子代理列表）
 */
export function getSubagents(parentThreadId, recursive = false) {
  if (!parentThreadId) {
    return Promise.reject(new Error('parentThreadId 不能为空'))
  }
  const params = { parent_thread_id: parentThreadId }
  if (recursive) {
    params.recursive = 'true'
  }
  return apiClient.get('/ai-engine/subagents/', { params })
}
