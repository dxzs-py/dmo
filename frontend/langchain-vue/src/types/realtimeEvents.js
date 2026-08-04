/**
 * 实时同步事件类型定义（JSDoc）
 *
 * 与后端 event_schema.py 的 EventType 枚举保持一致。
 * 本文件仅导出 JSDoc 类型与常量，不导出工具函数（遵循 workspace 规范）。
 */

/**
 * 事件类型枚举（与后端 EventType value 对应，用作 ws_event_name）
 *
 * @enum {string}
 */
export const EventType = {
  // 会话通道事件
  SESSION_CREATED: 'session_created',
  SESSION_UPDATED: 'session_updated',
  SESSION_DELETED: 'session_deleted',

  // 消息通道事件
  MESSAGE_ADDED: 'message_added',
  MESSAGE_UPDATED: 'message_updated',
  MESSAGE_DELETED: 'message_deleted',

  // 流式生命周期事件
  STREAM_STARTED: 'stream_started',
  STREAM_EVENT: 'stream_event',
  STREAM_COMPLETED: 'stream_completed',
  STREAM_FINALIZED: 'stream_finalized',

  // 工具调用生命周期事件（7 个）
  TOOL_CALL_PENDING: 'tool_call_pending',
  TOOL_CALL_WAITING: 'tool_call_waiting',
  TOOL_CALL_RUNNING: 'tool_call_running',
  TOOL_CALL_COMPLETED: 'tool_call_completed',
  TOOL_CALL_FAILED: 'tool_call_failed',
  TOOL_CALL_TIMEOUT: 'tool_call_timeout',

  // 审批事件（6 个）
  APPROVAL_PENDING: 'approval_pending',
  APPROVAL_PROCESSING: 'approval_processing',
  APPROVAL_WAITING: 'approval_waiting',
  APPROVAL_APPROVED: 'approval_approved',
  APPROVAL_REJECTED: 'approval_rejected',
  APPROVAL_TIMEOUT: 'approval_timeout',
}

/**
 * 触发快照校对的关键事件集合
 *
 * 这些事件标志着状态流转的关键节点，事件处理后调用 useSnapshotSync.syncFromSnapshot()
 * 进行全量校对，修复可能丢失或乱序的中间事件。
 *
 * 审批事件纳入说明（刷新后审批中间态兜底）：
 *   - APPROVAL_PENDING：审批恢复流起点，刷新后从后端恢复 pending 审批时推送；
 *     若该事件回调失败将导致审批卡片整体缺失，需快照校对兜底。
 *   - APPROVAL_WAITING：审批中间态，刷新后可能丢失该状态导致 UI 滞留在 pending；
 *     需快照校对将 UI 推进到正确的 waiting 状态。
 *   - APPROVAL_APPROVED：审批终态，保持原有快照校对以收敛整个审批流。
 *
 * 注：dispatchEvent 阶段二在事件类型匹配时即触发快照校对（无论回调是否成功），
 * 因此增加中间态事件不会引入副作用，仅作为兜底校对入口。
 */
export const SNAPSHOT_TRIGGER_EVENTS = new Set([
  EventType.STREAM_FINALIZED,
  EventType.TOOL_CALL_COMPLETED,
  EventType.APPROVAL_PENDING,
  EventType.APPROVAL_WAITING,
  EventType.APPROVAL_APPROVED,
])

/**
 * 工具调用生命周期事件类型集合
 */
export const TOOL_CALL_EVENT_TYPES = new Set([
  EventType.TOOL_CALL_PENDING,
  EventType.TOOL_CALL_WAITING,
  EventType.TOOL_CALL_RUNNING,
  EventType.TOOL_CALL_COMPLETED,
  EventType.TOOL_CALL_FAILED,
  EventType.TOOL_CALL_TIMEOUT,
  EventType.TOOL_CALL_REJECTED,
])

/**
 * 审批事件类型集合
 */
export const APPROVAL_EVENT_TYPES = new Set([
  EventType.APPROVAL_PENDING,
  EventType.APPROVAL_PROCESSING,
  EventType.APPROVAL_WAITING,
  EventType.APPROVAL_APPROVED,
  EventType.APPROVAL_REJECTED,
  EventType.APPROVAL_TIMEOUT,
])

/**
 * 实时事件基础结构
 *
 * @typedef {Object} RealtimeEvent
 * @property {string} type - 事件类型（EventType 枚举值）
 * @property {number} [seq] - 事件序号（合成事件无此字段）
 * @property {number} [timestamp] - 事件时间戳（毫秒）
 * @property {Object} payload - 事件载荷
 * @property {string} [payload.sessionId] - 会话 ID（schema 必填字段）
 * @property {string} [payload.taskId] - 任务 ID（独立深度研究场景）
 * @property {string} [sessionId] - 顶层 sessionId（部分事件使用）
 */

/**
 * 工具调用生命周期事件 payload
 *
 * 注意：payload 在 handleSessionEvent.applySessionEvent 入口处
 * 已通过 toCamelCase 统一转换，下游收到的均为 camelCase。
 *
 * @typedef {Object} ToolCallLifecyclePayload
 * @property {string} sessionId - 会话 ID
 * @property {string} [taskId] - 任务 ID
 * @property {string} toolCallId - 工具调用 ID
 * @property {string} [interruptId] - 中断 ID（审批场景）
 * @property {string} [name] - 工具名称
 * @property {string} [toolName] - 工具名称
 * @property {string} state - 工具调用状态（input-available/output-available/output-error 等）
 * @property {string} [status] - 前端映射后的 ToolCallStatus
 * @property {Object} [parameters] - 工具调用参数
 * @property {Object} [args] - 工具调用参数（别名）
 * @property {*} [result] - 工具调用结果
 * @property {*} [output] - 工具调用结果（别名）
 * @property {string} [error] - 错误信息
 * @property {string} [messageBackendId] - 关联消息的 backendId（前端附加）
 * @property {string} [messageId] - 关联消息 ID（后端字段）
 */

/**
 * 审批事件 payload
 *
 * 注意：payload 在 handleSessionEvent.applySessionEvent 入口处
 * 已通过 toCamelCase 统一转换，下游收到的均为 camelCase。
 *
 * @typedef {Object} ApprovalPayload
 * @property {string} sessionId - 会话 ID
 * @property {string} [taskId] - 任务 ID
 * @property {string} interruptId - 中断 ID（= toolCallId）
 * @property {string} toolCallId - 工具调用 ID
 * @property {string} [toolName] - 工具名称
 * @property {string} state - 审批状态（pending/processing/waiting/approved/rejected/timeout）
 * @property {string} [source] - 审批来源（chat/research/learning）
 * @property {string} [sourceId] - 来源 ID
 * @property {string} [operation] - 审批操作内容
 * @property {string} [command] - 审批命令（兼容字段）
 * @property {string} [action] - 审批动作（confirm_with_input 等）
 * @property {string} [dangerLevel] - 风险等级（medium/high）
 * @property {string} [title] - 审批标题
 * @property {string} [description] - 审批描述
 * @property {Object} [parameters] - 工具调用参数
 * @property {string} [graphInterruptId] - 批量审批的图中断 ID
 * @property {string} [inputPlaceholder] - 输入框占位文案
 * @property {string} [messageBackendId] - 关联消息的 backendId（前端附加）
 */

/**
 * 流式事件 payload
 *
 * 注意：payload 在 handleSessionEvent.applySessionEvent 入口处
 * 已通过 toCamelCase 统一转换。
 *
 * @typedef {Object} StreamPayload
 * @property {string} sessionId - 会话 ID
 * @property {string} [messageId] - 消息 ID
 * @property {string} [content] - 流式内容
 * @property {string} [reasoning] - 推理内容
 * @property {string} [streamState] - 流式状态（streaming/completed/finalized 等）
 * @property {boolean} [finalized] - 是否已完成 finalize
 * @property {Object} [usage] - token 用量
 * @property {number} [duration] - 推理耗时（秒）
 */

/**
 * 实时事件回调函数类型
 *
 * @typedef {(event: RealtimeEvent) => void | Promise<void>} RealtimeEventCallback
 */

/**
 * WebSocket 连接状态枚举
 *
 * @enum {string}
 */
export const RealtimeConnectionStatus = {
  CONNECTING: 'connecting',
  CONNECTED: 'connected',
  DISCONNECTED: 'disconnected',
  RECONNECTING: 'reconnecting',
}
