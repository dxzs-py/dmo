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
  REJECTED: 'rejected',
  TIMEOUT: 'timeout',
}

/**
 * 深度研究任务状态（后端 ResearchTask.status，snake_case 协议值）
 *
 * 协议值不参与 toCamelCase 转换（值非键名），前端通过本常量统一引用，
 * 避免在组件中散落裸 snake_case 字符串。
 */
export const ResearchTaskStatus = {
  PENDING: 'pending',
  AWAITING_APPROVAL: 'awaiting_approval',
  RUNNING: 'running',
  COMPLETED: 'completed',
  FAILED: 'failed',
}

/** 学习工作流任务状态（后端 WorkflowSession.status，snake_case 协议值） */
export const LearningTaskStatus = {
  RUNNING: 'running',
  WAITING_FOR_ANSWERS: 'waiting_for_answers',
  RETRY: 'retry',
  COMPLETED: 'completed',
  FAILED: 'failed',
}

/** 学习工作流阶段（后端 current_step，snake_case 协议值） */
export const LearningStep = {
  START: 'start',
  PLANNER: 'planner',
  RETRIEVAL: 'retrieval',
  QUIZ_GENERATOR: 'quiz_generator',
  WAITING_FOR_ANSWERS: 'waiting_for_answers',
  GRADING: 'grading',
  FEEDBACK: 'feedback',
  FEEDBACK_COMPLETED: 'feedback_completed',
  END: 'end',
  COMPLETED: 'completed',
  FAILED: 'failed',
}

/** 学习工作流题型（后端 question.type，snake_case 协议值） */
export const LearningQuestionType = {
  MULTIPLE_CHOICE: 'multiple_choice',
  FILL_BLANK: 'fill_blank',
  SHORT_ANSWER: 'short_answer',
}

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
  CHAIN_OF_THOUGHT: 'chain_of_thought',
  SUGGESTIONS: 'suggestions',
  END: 'end',
  ERROR: 'error',
}

// ==================== 工具调用与审批类型定义（统一字段规范） ====================

/**
 * @typedef {Object} ToolCallData
 * 工具调用事件的标准前端数据结构。
 *
 * 字段来源：后端 WebSocket 推送的 tool_call_* 事件 payload（snake_case），
 * 由 handleSessionEvent.js 入口统一调用 toCamelCase 转换，下游均为 camelCase。
 *
 * @property {string} id - 工具调用唯一标识（= toolCallId）
 * @property {string} toolCallId - 工具调用唯一标识（后端 LLM tool_call.id → toolCallId）
 * @property {string} name - 工具名称（去前缀显示用，= toolName）
 * @property {string} toolName - 工具全名（后端注册名，如 langchain_tools.shell_exec）
 * @property {Object} [parameters] - 工具入参（非空对象时才设置，{@link isNonEmptyParams} 判断）
 * @property {string} state - 工具状态（生命周期原始状态）
 * @property {string} status - 工具状态（ToolCallStatus 枚举值）
 * @property {string} [result] - 工具输出结果
 * @property {string} [error] - 错误信息
 * @property {boolean} isInternal - 是否为内部工具（只读工具标识）
 * @property {boolean} isAutoApproved - SAFE 级自动通过标记（SSE tool 事件经 utils/sse.js 由 autoApproved 统一改名，与 WebSocket 路径 toolCallHandler.js 消费端字段一致）
 * @property {string} parentToolCallId - 父级工具调用 ID（子 agent 场景）
 * @property {number} depth - 嵌套深度（0 = 顶层）
 * @property {string} agentName - 子 agent 名称
 * @property {string[]} [agentPath] - agent 调用路径链（工具事件 payload 已删除该字段，
 * 仅审批事件数据 approvalData.agentPath 携带，ToolCallCard 展示层级时回退读取）
 * @property {string} riskCeiling - 风险等级上限
 *
 * @see toolCallHandler.js - createHandleToolCallEvent 构建此结构
 * @see ToolCallCard.vue - 消费此结构渲染工具调用卡片
 */

/**
 * @typedef {Object} ApprovalData
 * 审批事件的标准前端数据结构。
 *
 * 字段来源：后端 approval_* 事件 payload，由 handleSessionEvent.js 入口统一
 * 调用 toCamelCase 转换，下游均为 camelCase。
 *
 * @property {string} interruptId - 审批中断 ID（resume 端点 KEY）
 * @property {string} graphInterruptId - 批次 ID（ApprovalMiddleware 生成）
 * @property {string} toolCallId - 关联的工具调用 ID
 * @property {string} toolName - 工具名称
 * @property {string} state - 审批状态（ApprovalState 枚举值）
 * @property {string} [description] - 审批描述文案
 * @property {string} [riskLevel] - 风险等级（safe/controlled/high，唯一权威字段）
 * @property {Object} [parameters] - 工具入参（前端展示用）
 * @property {string} [operation] - 待执行操作描述
 *
 * @see approvalHandler.js - createHandleApprovalEvent 处理此结构
 * @see ToolCallCard.vue - 审批面板消费此结构
 */


