export const AgentMode = {
  AGENT: 'agent',
  DEEP_RESEARCH: 'deep-research',
}

export const MessageRole = {
  USER: 'user',
  ASSISTANT: 'assistant',
  SYSTEM: 'system',
}

export const ToolCallStatus = {
  PENDING: 'pending',
  WAITING: 'waiting',
  RUNNING: 'running',
  COMPLETED: 'completed',
  FAILED: 'failed',
  PENDING_APPROVAL: 'pending_approval',
  APPROVED: 'approved',
  REJECTED: 'rejected',
  TIMEOUT: 'timeout',
  PROCESSING: 'processing',
}

/** 受保护的 toolCall status 集合，这些状态不应被 SSE 流中的 status/state 覆盖 */
export const PROTECTED_STATUSES = [
  ToolCallStatus.PENDING_APPROVAL,
  ToolCallStatus.APPROVED,
  ToolCallStatus.COMPLETED,
  ToolCallStatus.PROCESSING,
  ToolCallStatus.REJECTED,
  ToolCallStatus.TIMEOUT,
]

/** @deprecated 使用 PROTECTED_STATUSES 代替 */
export const APPROVAL_STATUSES = PROTECTED_STATUSES

/**
 * 只读工具名称集合
 *
 * 这些工具无副作用（不修改文件系统、不执行命令），后端不发起审批，
 * 前端使用 INTERNAL_DISPLAY_CONFIG 简化折叠样式渲染。
 *
 * 注意：read_file 虽然是读操作，但可能返回大量/敏感内容，
 * 仍走完整显示流程，不纳入只读集合。
 *
 * 有副作用工具（write_file/edit_file/execute/write_todos/task 等）
 * 不在此集合中，其审批面板的显示由 approvalData 驱动。
 */
export const READONLY_TOOL_NAMES = new Set([
  'glob',
  'grep',
  'ls',
])

/** 审批 state 常量（approval.state 字段使用，与 toolCall.status 语义不同） */
export const ApprovalState = {
  PENDING: 'pending',
  PROCESSING: 'processing',
  WAITING: 'waiting',
  APPROVED: 'approved',
  REJECTED: 'rejected',
  TIMEOUT: 'timeout',
}

/**
 * 消息流式状态（流式生命周期的状态机）
 *
 * 状态流转：
 *   falsy → STREAMING → FINALIZING → SYNCING → COMPLETED
 *   STREAMING → INTERRUPTED → STREAMING（恢复）
 *   任意 → ERROR
 *
 * streamState 是消息的流式状态字段，用于控制流式期间的合并保护与 UI 状态显示。
 */
export const StreamState = {
  STREAMING: 'streaming',
  INTERRUPTED: 'interrupted',
  FINALIZING: 'finalizing',
  SYNCING: 'syncing',
  COMPLETED: 'completed',
  ERROR: 'error',
}

/**
 * 受保护的流式状态集合
 *
 * 本地已进入流式流程的消息，其 content/toolCalls 等字段不应被后端快照粗暴覆盖，
 * 因为本地流式数据可能比快照更新、更完整。
 *
 * - STREAMING/INTERRUPTED/FINALIZING/SYNCING：流式中间态，本地数据领先
 * - COMPLETED/ERROR：流式终态，本地已完成，后端快照仅用于补全非内容字段
 */
export const PROTECTED_STREAM_STATES = new Set([
  StreamState.STREAMING,
  StreamState.INTERRUPTED,
  StreamState.FINALIZING,
  StreamState.SYNCING,
  StreamState.COMPLETED,
  StreamState.ERROR,
])

/** 将 approval state 映射到 toolCall status（统一映射函数） */
export function mapApprovalStateToStatus(state) {
  if (state === ApprovalState.APPROVED) return ToolCallStatus.APPROVED
  if (state === ApprovalState.REJECTED) return ToolCallStatus.REJECTED
  if (state === ApprovalState.TIMEOUT) return ToolCallStatus.TIMEOUT
  if (state === ApprovalState.PENDING) return ToolCallStatus.PENDING_APPROVAL
  if (state === ApprovalState.PROCESSING) return ToolCallStatus.PROCESSING
  return state
}

export const PlanStepStatus = {
  PENDING: 'pending',
  IN_PROGRESS: 'in_progress',
  COMPLETED: 'completed',
  FAILED: 'failed',
}

export const ChainOfThoughtStepStatus = {
  PENDING: 'pending',
  ACTIVE: 'active',
  COMPLETE: 'complete',
}

export const QueueItemStatus = {
  PENDING: 'pending',
  COMPLETED: 'completed',
}

export const StreamChunkType = {
  START: 'start',
  CHUNK: 'chunk',
  TOOL: 'tool',
  TOOL_RESULT: 'tool_result',
  REASONING: 'reasoning',
  SOURCE: 'source',
  SOURCES: 'sources',
  PLAN: 'plan',
  TASK: 'task',
  QUEUE: 'queue',
  CONTEXT: 'context',
  CITATION: 'citation',
  CHAIN_OF_THOUGHT: 'chainOfThought',
  SUGGESTIONS: 'suggestions',
  END: 'end',
  ERROR: 'error',
}

export function createMessage(id, role, content, extra = {}) {
  return {
    id: id || Date.now().toString(),
    role,
    content,
    timestamp: new Date().toISOString(),
    ...extra,
  }
}

export function createUserMessage(content) {
  return createMessage(Date.now().toString(), MessageRole.USER, content)
}

export function createAssistantMessage(content = '', extra = {}) {
  return createMessage((Date.now() + 1).toString(), MessageRole.ASSISTANT, content, extra)
}

export function parseStreamChunk(data) {
  try {
    return typeof data === 'string' ? JSON.parse(data) : data
  } catch (e) {
    return null
  }
}

export function isValidMode(mode) {
  return Object.values(AgentMode).includes(mode)
}

export function validateProp(value, typeName, required = false) {
  if (required && (value === undefined || value === null)) return false
  const typeMap = {
    String: (v) => typeof v === 'string',
    Number: (v) => typeof v === 'number' && !isNaN(v),
    Boolean: (v) => typeof v === 'boolean',
    Array: (v) => Array.isArray(v),
    Object: (v) => v !== null && typeof v === 'object' && !Array.isArray(v),
    Function: (v) => typeof v === 'function',
  }
  const validator = typeMap[typeName]
  return validator ? validator(value) : true
}

export function validateMessage(msg) {
  if (!msg || typeof msg !== 'object') return false
  if (!msg.id || typeof msg.id !== 'string') return false
  if (!Object.values(MessageRole).includes(msg.role)) return false
  if (msg.content === undefined || typeof msg.content !== 'string') return false
  return true
}


