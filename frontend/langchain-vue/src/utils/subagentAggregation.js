/**
 * 子代理数据聚合（spec D10 前端渲染数据源）
 *
 * 将消息上的散落数据聚合为 SubAgentCard 所需的子代理视图数组：
 * - 子代理元数据（status/resultPreview/pendingInterruptInfo/task/spawnToolCallId）
 *   来自 GET 接口（经 axios 拦截器 toCamelCase）；
 * - 子代理工具调用来自 message.toolCalls（toolCall.subagentThreadId 过滤）；
 * - 子代理正文/思考来自 message.subagentContents[threadId]（事件增量累计）；
 * - task 回退：主 agent spawn_sub_agent 工具 input（对象或 JSON 字符串）的 task 字段。
 *
 * 主 Agent 的 toolCalls（subagentThreadId 为空）不进入任何子代理视图。
 *
 * 顺序关联（mapSubagentsBySpawnToolCall）：spawnToolCallId ↔ 主 agent spawn
 * 工具 toolCall.id 同值（均源自后端 tool_call_id = LLM tool_call.id），
 * 渲染层据此将子代理挂载到对应 spawn 工具调用之后。
 */
import { SUBAGENT_STATUS } from './subagentStatus.js'
import { ApprovalState } from '../types/index.js'
import { TERMINAL_STATUSES } from './toolCallStateMachine.js'

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
    return approvalState === ApprovalState.PENDING || approvalState === ApprovalState.WAITING
  })
  if (hasPendingApproval) return SUBAGENT_STATUS.INTERRUPTED_PENDING_USER_INPUT

  const allTerminal = toolCalls.every(tc => TERMINAL_STATUSES.has(tc.status))
  if (allTerminal) return SUBAGENT_STATUS.COMPLETED
  return SUBAGENT_STATUS.RUNNING
}

/**
 * 解析工具入参（input 可能为对象或 LLM 原文 JSON 字符串）。
 *
 * 使用 Object.prototype.toString.call 严格判断普通对象（工作区规范：
 * 禁止用 typeof 判断 object，防止 null/Date/RegExp 误入）。
 *
 * @param {Object|string|*} rawInput - 工具入参（toolCall.input / toolCall.parameters）
 * @returns {Object|null} 解析后的普通对象；无法解析返回 null
 */
function parseToolInput(rawInput) {
  if (!rawInput) return null
  if (Object.prototype.toString.call(rawInput) === '[object Object]') {
    return rawInput
  }
  if (typeof rawInput === 'string') {
    try {
      const parsed = JSON.parse(rawInput)
      return Object.prototype.toString.call(parsed) === '[object Object]' ? parsed : null
    } catch {
      return null
    }
  }
  return null
}

/**
 * 从主 agent 工具调用中提取 spawn_sub_agent 派生信息（task 回退数据源）。
 *
 * 仅统计主 agent 工具（subagentThreadId 为空）；子代理内部的 spawn 工具
 * 属于嵌套层，由递归面板各自处理。
 *
 * @param {Array} toolCalls - 消息全量工具调用（含主/子）
 * @returns {Array<{threadId: string, task: string, toolCallId: string}>}
 */
function collectSpawnEntries(toolCalls) {
  const entries = []
  for (const tc of toolCalls) {
    if (!tc || tc.subagentThreadId) continue
    if (tc.name !== 'spawn_sub_agent') continue
    const parsed = parseToolInput(tc.input || tc.parameters)
    if (!parsed) continue
    entries.push({
      threadId: typeof parsed.threadId === 'string' ? parsed.threadId : '',
      task: typeof parsed.task === 'string' ? parsed.task.trim() : '',
      toolCallId: tc.id || tc.toolCallId || '',
    })
  }
  return entries
}

/**
 * 解析子代理 task 展示值（回退链：meta.task → spawn 工具 input.task → 空串）。
 *
 * spawn 关联策略（保守不错配）：
 * 1. spawn input 携带 threadId 且与子代理 threadId 相等 → 精确取 task；
 * 2. 无 thread 关联信息但全消息仅一个含 task 的 spawn → 归属唯一，取 task；
 * 3. 多 spawn 且无 thread 关联信息 → 无法判定归属，返回空串（标题回退 agentName）。
 *
 * @param {string} metaTask - GET 接口元数据中的 task（后端权威来源）
 * @param {string} threadId - 子代理线程 ID
 * @param {Array<{threadId: string, task: string}>} spawnEntries - 主 agent spawn 工具信息
 * @returns {string}
 */
function resolveSubagentTask(metaTask, threadId, spawnEntries) {
  if (metaTask) return metaTask
  const exact = spawnEntries.find(e => e.threadId && e.threadId === threadId && e.task)
  if (exact) return exact.task
  const withTask = spawnEntries.filter(e => e.task)
  if (withTask.length === 1) return withTask[0].task
  return ''
}

/**
 * 聚合消息的子代理视图数组。
 *
 * @param {Object} message - 前端消息对象（含 toolCalls / subagentContents / backendId / id）
 * @param {Array} metaList - 该消息关联的子代理元数据列表（assistantMessageId 已匹配）；
 *   每项含 task（中文任务描述）与 spawnToolCallId（派生它的主 agent spawn 工具 ID，可空）
 * @returns {Array<{threadId, agentName, task, spawnToolCallId, status, resultPreview, pendingInterruptInfo, toolCalls, content, reasoningContent}>}
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

  const spawnEntries = collectSpawnEntries(toolCalls)

  const subagents = []
  for (const threadId of threadIds) {
    const meta = metaByThreadId[threadId] || {}
    const subToolCalls = toolCalls.filter(tc => tc.subagentThreadId === threadId)
    const contentEntry = subagentContents[threadId] || {}

    subagents.push({
      threadId,
      agentName: meta.agentName || contentEntry.agentName || '子代理',
      // task 回退链：meta.task → spawn 工具 input.task → 空串
      task: resolveSubagentTask(meta.task || '', threadId, spawnEntries),
      // spawnToolCallId 仅来自后端元数据（GET /subagents 的 spawn_tool_call_id）
      spawnToolCallId: meta.spawnToolCallId || '',
      // 父线程 ID（主层指向会话/任务 ID，嵌套层指向父子代理 threadId）
      parentThreadId: meta.parentThreadId || '',
      // 嵌套深度：后端序列化权威字段（主 agent=0，子=1，孙=2；历史实例兜底 1）
      depth: meta.depth || 1,
      status: meta.status || deriveSubagentStatus(subToolCalls),
      resultPreview: meta.resultPreview || '',
      pendingInterruptInfo: meta.pendingInterruptInfo || null,
      toolCalls: subToolCalls,
      content: contentEntry.content || '',
      reasoningContent: contentEntry.reasoningContent || '',
    })
  }

  // 全局序号（按聚合顺序 1-based）：区分同一任务内多个子代理；
  // depth 由后端序列化提供，前端不再推导
  subagents.forEach((subagent, i) => {
    subagent.order = i + 1
  })

  return subagents
}

/**
 * 按 spawnToolCallId 建立子代理索引（聚合层顺序关联）。
 *
 * 供渲染层将子代理挂载到派生它的 spawn_sub_agent 工具调用之后：
 * - bySpawnToolCallId: spawnToolCallId → subagent（用于 InlineToolCallContent
 *   工具插槽内 toolCall.id 精确匹配）；
 * - orphans: 无 spawnToolCallId 的孤儿（由宿主在主工具流末尾兜底渲染，
 *   本组件/面板不处理孤儿）。
 *
 * @param {Array} subagents - 子代理视图数组（buildSubagentsFromMessage 产物）
 * @returns {{bySpawnToolCallId: Map<string, Object>, orphans: Array}}
 */
export function mapSubagentsBySpawnToolCall(subagents) {
  const bySpawnToolCallId = new Map()
  const orphans = []
  for (const subagent of subagents || []) {
    if (!subagent) continue
    if (subagent.spawnToolCallId) {
      bySpawnToolCallId.set(subagent.spawnToolCallId, subagent)
    } else {
      orphans.push(subagent)
    }
  }
  return { bySpawnToolCallId, orphans }
}

/**
 * 格式化子代理标题（SubAgentCard 头部共用）。
 *
 * 优先展示中文任务描述（超长截断加省略号），回退 agentName，再回退「子代理」。
 *
 * @param {string} task - 中文任务描述（可为空）
 * @param {string} agentName - 子代理名称（可为空）
 * @param {number} [maxLength=30] - task 截断长度上限
 * @returns {string}
 */
export function formatSubagentTitle(task, agentName, maxLength = 30) {
  const trimmedTask = typeof task === 'string' ? task.trim() : ''
  if (trimmedTask) {
    return trimmedTask.length > maxLength
      ? `${trimmedTask.slice(0, maxLength)}…`
      : trimmedTask
  }
  return agentName || '子代理'
}
