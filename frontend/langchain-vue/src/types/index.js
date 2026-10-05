/**
 * 前端类型定义与枚举常量（唯一权威源）
 *
 * 结构：枚举常量区（export const 枚举/只读 Set）→ JSDoc 类型区（typedef）。
 *
 * 约束：本文件禁止导出工具函数（SpecificationRequirements 规范明文）——
 * 工具函数归属 utils/；运行时校验统一走 utils/validation.js 的 zod 体系
 * （其 role 枚举引用本文件 MessageRole，保持单一权威源）。
 */

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

/**
 * 学习工作流步骤（镜像后端 `learning/models.py` 的 `WorkflowStep`，snake_case 协议值）。
 *
 * ⚠️ 必须与后端枚举**逐值对齐**：此前本枚举缺 7 个后端实际写入的值
 * （planner_completed / planner_error / quiz_error / grading_completed /
 * grading_error / feedback_error / unknown），又多出后端从不写入的值，
 * 导致进度条与终态判定失准、用户看到裸英文标识符。
 * 新增/删除步骤时前后端必须同步修改。
 *
 * 分组：阶段中 / 节点完成 / 节点失败 / 中断重试 / 终态与兼容值。
 */
export const LearningStep = {
  // —— 阶段中（进度条展示顺序）——
  START: 'start',
  PLANNER: 'planner',
  RETRIEVAL: 'retrieval',
  QUIZ_GENERATOR: 'quiz_generator',
  GRADING: 'grading',
  FEEDBACK: 'feedback',

  // —— 节点完成 ——
  PLANNER_COMPLETED: 'planner_completed',
  GRADING_COMPLETED: 'grading_completed',
  FEEDBACK_COMPLETED: 'feedback_completed',

  // —— 节点失败 ——
  PLANNER_ERROR: 'planner_error',
  QUIZ_ERROR: 'quiz_error',
  GRADING_ERROR: 'grading_error',
  FEEDBACK_ERROR: 'feedback_error',

  // —— 中断 / 重试 ——
  WAITING_FOR_ANSWERS: 'waiting_for_answers',
  RETRY: 'retry',

  // —— 终态与兼容值（后端仅作防御性判断，实际终态是 FEEDBACK_COMPLETED）——
  COMPLETED: 'completed',
  FAILED: 'failed',
  END: 'end',
  UNKNOWN: 'unknown',
}

/**
 * 步骤 → 所属"阶段"的归并（用于进度条）。
 *
 * 后端节点写入的是**节点结果**（如 quiz_error / feedback_completed），
 * 而进度条按**阶段**（quiz_generator / feedback）排序，故需归并：
 * 未归并时 `stepOrder.indexOf('quiz_error') === -1` → 进度条显示"无已完成步骤"。
 */
const STEP_STAGE_ALIASES = {
  [LearningStep.PLANNER_COMPLETED]: LearningStep.PLANNER,
  [LearningStep.PLANNER_ERROR]: LearningStep.PLANNER,
  [LearningStep.GRADING_COMPLETED]: LearningStep.GRADING,
  [LearningStep.GRADING_ERROR]: LearningStep.GRADING,
  [LearningStep.FEEDBACK_ERROR]: LearningStep.FEEDBACK,
  [LearningStep.QUIZ_ERROR]: LearningStep.QUIZ_GENERATOR,
}

/** 把节点结果值归并到其所属阶段值；无对应阶段则原样返回。 */
export function toWorkflowStage(step) {
  return STEP_STAGE_ALIASES[step] || step
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
 * @property {Object} [parameters] - 工具入参（非空对象时才设置）
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


