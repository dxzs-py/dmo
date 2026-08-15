/**
 * 子代理数据聚合（spec D10 前端渲染数据源）
 *
 * 将消息上的散落数据聚合为 SubAgentCard 所需的子代理视图数组：
 * - 子代理元数据（status/resultPreview/pendingInterruptInfo）来自 GET 接口；
 * - 子代理工具调用来自 message.toolCalls（toolCall.subagentThreadId 过滤）；
 * - 子代理正文/思考来自 message.subagentContents[threadId]（事件增量累计）。
 *
 * 主 Agent 的 toolCalls（subagentThreadId 为空）不进入任何子代理视图。
 */
import { SUBAGENT_STATUS } from './subagentStatus'

/**
 * 从 toolCalls 兜底推导子代理状态（元数据未拉取到位时的展示兜底）。
 * 权威状态以后端 SubAgentInstance.status（GET 接口）为准。
 *
 * @param {Array} toolCalls
 * @returns {string} 子代理状态枚举值
 */
function deriveSubagentStatus(toolCalls) {
  if (!toolCalls || toolCalls.length === 0) return SUBAGENT_STATUS.RUNNING
  const hasPendingApproval = toolCalls.some((tc) => {
    const approvalState = tc.approval?.state
    return approvalState === 'pending' || approvalState === 'waiting'
  })
  if (hasPendingApproval) return SUBAGENT_STATUS.INTERRUPTED_PENDING_USER_INPUT

  const allTerminal = toolCalls.every(tc =>
    ['completed', 'failed', 'rejected', 'timeout'].includes(tc.status)
  )
  if (allTerminal) return SUBAGENT_STATUS.COMPLETED
  return SUBAGENT_STATUS.RUNNING
}

/**
 * 聚合消息的子代理视图数组。
 *
 * @param {Object} message - 前端消息对象（含 toolCalls / subagentContents / backendId / id）
 * @param {Array} metaList - 该消息关联的子代理元数据列表（assistantMessageId 已匹配）
 * @returns {Array<{threadId, agentName, status, resultPreview, pendingInterruptInfo, toolCalls, content, reasoningContent}>}
 */
export function buildSubagentsFromMessage(message, metaList = []) {
  const toolCalls = Array.isArray(message?.toolCalls) ? message.toolCalls : []
  const subagentContents = message?.subagentContents || {}

  const metaByThreadId = {}
  const threadIds = new Set()

  for (const meta of metaList) {
    if (!meta || !meta.threadId) continue
    threadIds.add(meta.threadId)
    metaByThreadId[meta.threadId] = meta
  }
  // 事件先到、元数据后到的时序兜底：从 toolCalls.subagentThreadId 收集
  for (const tc of toolCalls) {
    if (tc.subagentThreadId) threadIds.add(tc.subagentThreadId)
  }

  const subagents = []
  for (const threadId of threadIds) {
    const meta = metaByThreadId[threadId] || {}
    const subToolCalls = toolCalls.filter(tc => tc.subagentThreadId === threadId)
    const contentEntry = subagentContents[threadId] || {}

    subagents.push({
      threadId,
      agentName: meta.agentName || contentEntry.agentName || '子代理',
      status: meta.status || deriveSubagentStatus(subToolCalls),
      resultPreview: meta.resultPreview || '',
      pendingInterruptInfo: meta.pendingInterruptInfo || null,
      toolCalls: subToolCalls,
      content: contentEntry.content || '',
      reasoningContent: contentEntry.reasoningContent || '',
    })
  }

  return subagents
}
