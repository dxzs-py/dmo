/**
 * 子代理（SubAgentRuntime）API 模块
 *
 * 对接后端 /api/v1/ai-engine/subagents/ 端点：
 * - GET  /subagents/?parent_thread_id=xxx：查询父线程下的子代理列表（SubAgentCard 数据源）
 * - POST /subagents/resume/：恢复中断的子代理（断点续跑）
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

/**
 * 恢复中断的子代理（SubAgentRuntime 断点续跑，与主会话 resume 隔离）。
 *
 * @param {string} subagentThreadId - 子代理独立 thread_id（SubAgentInstance.thread_id）
 * @param {Object} resumePayload - 审批决策 payload（batch 决策 dict：{ tool_call_id: boolean }）
 * @param {{ signal?: AbortSignal }} [options] - 透传给 apiClient.post 的选项
 * @returns {Promise} axios response
 */
export function resumeSubagent(subagentThreadId, resumePayload, options = {}) {
  if (!subagentThreadId) {
    return Promise.reject(new Error('subagentThreadId 不能为空'))
  }
  return apiClient.post('/ai-engine/subagents/resume/', {
    subagent_thread_id: subagentThreadId,
    resume_payload: resumePayload,
  }, options)
}
