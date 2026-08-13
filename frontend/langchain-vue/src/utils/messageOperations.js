import { PROTECTED_STREAM_STATES, ToolCallStatus, ApprovalState } from '../types/index.js'
import {
  applyToolCallState,
  isTerminalStatus,
  TERMINAL_STATUSES,
  NON_TERMINAL_STATUSES,
} from './toolCallStateMachine.js'

// 状态机导出（终态判定等）统一收敛到 toolCallStateMachine.js（唯一权威），
// 此处 re-export 保持既有调用方（session.js / research.js）导入路径不变。
export { isTerminalStatus }

// ==================== 标准化常量（统一所有模块的工具调用状态判断） ====================

/**
 * 非终态审批状态集合（流式完成/最终化时需清理为终态）
 *
 * 与 stores/sync/constants.js 的 NON_TERMINAL_APPROVAL_STATES 保持一致，
 * 此处内联定义避免 utils → stores 的反向依赖（循环依赖风险）。
 */
const _NON_TERMINAL_APPROVAL_STATES = [
  ApprovalState.PENDING,
  ApprovalState.PROCESSING,
  ApprovalState.WAITING,
]

/**
 * 在 toolCall 数组中查找与 data 精确匹配的条目（仅 id / toolCallId 精确匹配）
 *
 * 幂等合并的唯一定位依据：id（或 toolCallId）精确匹配。
 * 禁止 name 回退合并（Task 5.2）：并行调用两个同名工具时，无 id 的 data 若按 name
 * 合并会命中同名的最后一个未完成条目，导致 count 2→1 数据丢失。
 * data 无 id 时一律返回 -1（调用方新建独立条目，语义为"新建待绑定"），
 * 绝不与已有同名工具合并。
 *
 * @param {Array} toolCalls - toolCall 数组
 * @param {Object} data - 工具事件数据
 * @returns {number} 匹配到的索引；-1 表示无匹配（新建）
 */
function _findMatchingToolCall(toolCalls, data) {
  if (!toolCalls || toolCalls.length === 0) return -1
  const targetId = data.id || data.toolCallId
  if (targetId) {
    return toolCalls.findIndex(t => t.id === targetId || t.toolCallId === targetId)
  }
  return -1
}

/**
 * 统一的 toolCall 匹配函数（用于审批相关场景）
 *
 * 匹配策略（5级回退）：
 *   0. llm_tool_call_id 精确匹配（后端注入的 LLM tool_call.id，最可靠）
 *   1. id 精确匹配（call_xxx 或 interrupt_id）
 *   1.5 approval 内的 interrupt_id/tool_call_id 匹配
 *   2. toolName + operation 内容匹配
 *   3. toolName 宽松匹配（取最新未审批的）
 *
 * @param {Array} toolCalls - toolCall 数组
 * @param {string} toolCallId - 待匹配的 ID
 * @param {Object} options - 匹配选项
 * @param {Object} options.approvalData - 审批数据（用于 llm_tool_call_id 和 toolName 回退匹配）
 * @param {boolean} options.skipApproved - 是否跳过已审批的 toolCall（默认 true）
 * @returns {Object|null} 匹配到的 toolCall
 */
export function findToolCallById(toolCalls, toolCallId, options = {}) {
  if (!toolCalls || toolCalls.length === 0 || !toolCallId) return null
  const { approvalData = null, skipApproved = true } = options
  const _getCmd = (t) => t.parameters?.command || t.args?.command
  const _getToolName = (t) => t.name || t.toolName || t.function?.name
  /** 判断 toolCall 是否处于审批终态（已确认/已拒绝/已超时/已完成） */
  const _isApprovalFinal = (t) => {
    if (!t.approval) return false
    const finalStates = ['approved', 'rejected', 'timeout', 'completed']
    return finalStates.includes(t.approval.state)
  }

  // 0. tool_call_id 精确匹配（最可靠，匹配 t.id 和 t.toolCallId 两个维度）
  if (approvalData?.toolCallId) {
    const approvalToolCallId = approvalData.toolCallId
    const tc = toolCalls.find(t => t.id === approvalToolCallId || t.toolCallId === approvalToolCallId)
    if (tc) return tc
    // 精确匹配失败保护：tool_call_id 存在但未匹配到 → 立即返回 null，
    // 禁止进入 toolName 回退匹配（避免将审批数据错误绑定到其他同名工具）
    return null
  }
  // 1. id 精确匹配
  let tc = toolCalls.find(t => t.id === toolCallId)
  if (tc) return tc
  // 1.5 approval 内的 interrupt_id/tool_call_id 匹配
  tc = toolCalls.find(t =>
    t.approval && (t.approval.interruptId === toolCallId || t.approval.toolCallId === toolCallId)
  )
  if (tc) return tc
  // 2. toolName + operation 内容匹配
  if (approvalData?.toolName) {
    const _op = approvalData.operation || approvalData.command
    if (_op) {
      tc = toolCalls.find(t =>
        _getToolName(t) === approvalData.toolName &&
        (_getCmd(t) === _op || Object.values(t.parameters || {}).some(v => String(v) === _op)) &&
        (skipApproved ? !_isApprovalFinal(t) : true)
      )
      if (tc) return tc
    }
    // 3. toolName 宽松匹配
    for (let i = toolCalls.length - 1; i >= 0; i--) {
      const t = toolCalls[i]
      if (_getToolName(t) === approvalData.toolName && (skipApproved ? !_isApprovalFinal(t) : true)) {
        return t
      }
    }
  }
  return null
}

/**
 * 统一提取 interrupt ID
 * 优先级：interrupt_id > tool_call_id，因为 interrupt_id 是 LangGraph interrupt 的唯一标识
 * @param {Object} approval - 审批数据对象
 * @returns {string} interrupt ID
 */
export function getInterruptId(approval) {
  return approval?.interruptId || approval?.toolCallId || ''
}

function _addOrUpdateToolCallInMessage(message, data) {
  if (!message.toolCalls) message.toolCalls = []
  const idx = _findMatchingToolCall(message.toolCalls, data)
  if (idx >= 0) {
    // 合并逻辑统一委托 _mergeExistingToolCall（与 Map 版共用单一实现，
    // 消除数组/Map 双实现的 stateMap 推导漂移 —— Task 5.5）
    message.toolCalls[idx] = _mergeExistingToolCall(message.toolCalls[idx], data)
  } else {
    // 新建 toolCall（data 无 id 时也在此新建独立条目，禁止与同名工具合并 —— Task 5.2）
    message.toolCalls.push(_normalizeNewToolCall(data))
  }
}

function _updateOrAddToolResultInMessage(message, data) {
  if (!message.toolCalls) message.toolCalls = []
  const idx = _findMatchingToolCall(message.toolCalls, data)
  if (idx >= 0) {
    const existing = message.toolCalls[idx]
    const updates = {}
    if (data.state !== undefined) updates.state = data.state
    if (data.result !== undefined) updates.result = data.result
    if (data.error !== undefined) updates.error = data.error
    // 状态推进统一走状态机（result 模式：显式 status > 事件态映射 > result→COMPLETED > error→FAILED）。
    // 终态不回退 / 非法转换回退由 applyToolCallState 保证（核心层权威化，Task 2）。
    const { status: nextStatus, applied: statusApplied } = applyToolCallState(existing, data, { mode: 'result' })
    if (statusApplied && nextStatus !== undefined) {
      updates.status = nextStatus
      // 进入终态时清除审批状态，防止审批组件残留（P21/P22 修复）
      if (isTerminalStatus(nextStatus) && existing.approval) {
        updates.approval = null
      }
      // 进入终态时设置 completedAt（Task 16 P1 修复）
      if (isTerminalStatus(nextStatus) && !existing.completedAt) {
        updates.completedAt = new Date().toISOString()
      }
    }
    Object.assign(message.toolCalls[idx], updates)
  } else {
    const toolData = { ...data }
    // 状态推导统一走状态机（result 模式）；兜底默认 pending：与原级联的
    // else 分支（state→'pending' / 无 state→'pending'）一致
    const { status: nextStatus, applied: statusApplied } =
      applyToolCallState(null, toolData, { mode: 'result', fallback: ToolCallStatus.PENDING })
    if (statusApplied && nextStatus !== undefined) {
      toolData.status = nextStatus
      // 终态时设置 completedAt
      if (isTerminalStatus(nextStatus) && !toolData.completedAt) {
        toolData.completedAt = new Date().toISOString()
      }
    }
    message.toolCalls.push(toolData)
  }
}

/**
 * 匹配缓存的待审批数据到 toolCall（解决审批事件先于 tool 事件到达的时序问题）
 */
function _matchPendingApprovals(message, data) {
  if (!message._pendingApprovals || message._pendingApprovals.length === 0) return
  if (!message.toolCalls || message.toolCalls.length === 0) return

  const remaining = []
  for (const { toolCallId, approvalData } of message._pendingApprovals) {
    const matched = findToolCallById(message.toolCalls, toolCallId, { approvalData, skipApproved: true })

    if (matched) {
      matched.approval = approvalData
      // approvalData 用于审批面板显示，toolCall.status 仅由 tool_call_* 事件变更
      // 匹配成功后，移除对应的合成 toolCall（避免重复渲染）
      if (matched.isSynthetic !== true) {
        const syntheticIdx = message.toolCalls.findIndex(
          t => t.isSynthetic === true && t.id === toolCallId
        )
        if (syntheticIdx !== -1) {
          message.toolCalls.splice(syntheticIdx, 1)
        }
      }
    } else {
      remaining.push({ toolCallId, approvalData })
    }
  }

  if (remaining.length === 0) {
    delete message._pendingApprovals
  } else {
    message._pendingApprovals = remaining
  }
}

/**
 * 添加/更新工具调用到最后一条 assistant 消息（add 路径：工具创建/中间态入口）
 *
 * 对应 SSE tool 事件（pending/waiting/running 等中间态）。
 * 终态（completed/failed/timeout）应走 updateOrAddToolResultInLastMessage（result 路径）。
 *
 * @param {Array} sessions - 会话列表
 * @param {string} sessionId - 会话 ID
 * @param {Object} data - 工具事件数据
 */
export function addOrUpdateToolCallInLastMessage(sessions, sessionId, data) {
  const result = getLastAssistantMessage(sessions, sessionId)
  if (!result) return
  _addOrUpdateToolCallInMessage(result.message, data)
  const ver = result.message.versions?.[result.message.currentVersion]
  if (ver) _addOrUpdateToolCallInMessage(ver, data)

  _matchPendingApprovals(result.message, data)
  if (ver) _matchPendingApprovals(ver, data)
}

/**
 * 更新/添加工具结果到最后一条 assistant 消息（result 路径：工具结果唯一写入入口）
 *
 * 对应 SSE tool_result / WebSocket tool_call_completed/failed/timeout 事件。
 * 允许终态覆盖本地 PENDING（绕过 add 路径的 PENDING+非终态审批锁死保护）。
 * 中间态（pending/waiting/running）应走 addOrUpdateToolCallInLastMessage（add 路径）。
 *
 * @param {Array} sessions - 会话列表
 * @param {string} sessionId - 会话 ID
 * @param {Object} data - 工具结果事件数据
 */
export function updateOrAddToolResultInLastMessage(sessions, sessionId, data) {
  const result = getLastAssistantMessage(sessions, sessionId)
  if (!result) return
  _updateOrAddToolResultInMessage(result.message, data)
  const ver = result.message.versions?.[result.message.currentVersion]
  if (ver) _updateOrAddToolResultInMessage(ver, data)
}

/**
 * 添加/更新工具调用到指定消息（add 路径：工具创建/中间态入口）
 *
 * 对应 SSE tool 事件（pending/waiting/running 等中间态）。
 * 终态（completed/failed/timeout）应走 updateOrAddToolResultInMessageByIdx（result 路径）。
 *
 * @param {Array} sessions - 会话列表
 * @param {string} sessionId - 会话 ID
 * @param {number} messageIndex - 消息索引
 * @param {Object} data - 工具事件数据
 */
export function addOrUpdateToolCallInMessageByIdx(sessions, sessionId, messageIndex, data) {
  const result = getMessageByIndex(sessions, sessionId, messageIndex)
  if (!result) return
  _addOrUpdateToolCallInMessage(result.message, data)
  const ver = result.message.versions?.[result.message.currentVersion]
  if (ver) _addOrUpdateToolCallInMessage(ver, data)
}

/**
 * 更新/添加工具结果到指定消息（result 路径：工具结果唯一写入入口）
 *
 * 对应 SSE tool_result / WebSocket tool_call_completed/failed/timeout 事件。
 * 允许终态覆盖本地 PENDING（绕过 add 路径的 PENDING+非终态审批锁死保护）。
 * 中间态（pending/waiting/running）应走 addOrUpdateToolCallInMessageByIdx（add 路径）。
 *
 * @param {Array} sessions - 会话列表
 * @param {string} sessionId - 会话 ID
 * @param {number} messageIndex - 消息索引
 * @param {Object} data - 工具结果事件数据
 */
export function updateOrAddToolResultInMessageByIdx(sessions, sessionId, messageIndex, data) {
  const result = getMessageByIndex(sessions, sessionId, messageIndex)
  if (!result) return
  _updateOrAddToolResultInMessage(result.message, data)
  const ver = result.message.versions?.[result.message.currentVersion]
  if (ver) _updateOrAddToolResultInMessage(ver, data)
}

export function getLastAssistantMessage(sessions, sessionId) {
  const session = sessions.find(s => s.id === sessionId)
  if (!session || session.messages.length === 0) return null
  const last = session.messages[session.messages.length - 1]
  return last.role === 'assistant' ? { session, message: last } : null
}

export function setLastMessageField(sessions, sessionId, field, value) {
  const result = getLastAssistantMessage(sessions, sessionId)
  if (!result) return false
  result.message[field] = value
  const ver = result.message.versions?.[result.message.currentVersion]
  if (ver) ver[field] = value
  return true
}

export function addLastMessageFieldItem(sessions, sessionId, field, item) {
  const result = getLastAssistantMessage(sessions, sessionId)
  if (!result) return
  if (!result.message[field]) result.message[field] = []
  result.message[field].push(item)
  const ver = result.message.versions?.[result.message.currentVersion]
  if (ver) {
    if (!ver[field]) ver[field] = []
    ver[field].push(item)
  }
}

export function getMessageByIndex(sessions, sessionId, messageIndex) {
  const session = sessions.find(s => s.id === sessionId)
  if (!session || messageIndex < 0 || messageIndex >= session.messages.length) return null
  return { session, message: session.messages[messageIndex] }
}

export function setMessageField(sessions, sessionId, messageIndex, field, value) {
  const result = getMessageByIndex(sessions, sessionId, messageIndex)
  if (!result) return
  result.message[field] = value
  const ver = result.message.versions?.[result.message.currentVersion]
  if (ver) ver[field] = value
}

export function addMessageFieldItem(sessions, sessionId, messageIndex, field, item) {
  const result = getMessageByIndex(sessions, sessionId, messageIndex)
  if (!result) return
  if (!result.message[field]) result.message[field] = []
  result.message[field].push(item)
  const ver = result.message.versions?.[result.message.currentVersion]
  if (ver) {
    if (!ver[field]) ver[field] = []
    ver[field].push(item)
  }
}

export function appendToMessage(sessions, sessionId, messageIndex, content) {
  const result = getMessageByIndex(sessions, sessionId, messageIndex)
  if (!result) return
  result.message.content = (result.message.content || '') + content
  const ver = result.message.versions?.[result.message.currentVersion]
  if (ver) ver.content = result.message.content
}

export function createMessageVersion(message) {
  return {
    id: message.id,
    content: message.content,
    sources: message.sources || [],
    plan: message.plan || null,
    chainOfThought: message.chainOfThought || null,
    toolCalls: message.toolCalls || [],
    reasoning: message.reasoning || null,
    suggestions: message.suggestions || null,
    context: message.context || null,
    attachmentIds: message.attachmentIds || [],
    attachments: message.attachments || [],
    _pendingApprovals: message._pendingApprovals
      ? message._pendingApprovals.map(p => ({ ...p, approvalData: { ...p.approvalData } }))
      : undefined,
  }
}

// ==================== Map 操作函数（researchStore 用） ====================
//
// 与 sessionStore 的数组操作函数（addOrUpdateToolCallInLastMessage 等）对应，
// 但操作 Map<toolCallId, toolCall> 而非 message.toolCalls 数组。
//
// 设计意图：researchStore 以 toolCallMap 为唯一真相源，toolCalls 数组为派生（通过 _syncToolCalls 同步）。
// Map 的 key 为 toolCall.id || toolCall.toolCallId。
//
// 合并/保护逻辑与数组版本（_addOrUpdateToolCallInMessage / _updateOrAddToolResultInMessage）保持一致，
// 确保两个 store 行为对齐。

/**
 * 合并已有 toolCall 与新数据（保护审批状态）
 *
 * 抽取自 _addOrUpdateToolCallInMessage 的合并逻辑，Map 版本与数组版本共用。
 *
 * @param {Object} existing - 已有 toolCall
 * @param {Object} data - 新数据
 * @returns {Object} 合并后的 toolCall（新对象，不修改 existing）
 */
function _mergeExistingToolCall(existing, data) {
  const merged = { ...existing, ...data }
  // seq 保护：仅当 data 显式携带 number 类型 seq 时才保留（后端工具/审批事件
  // 从 ToolCallContext 透传的全局递增序号，跨浏览器统一排序依据）；data 未携带
  // seq（如 SSE tool_info）时回退 existing.seq，防止 undefined 覆盖已分配序号
  if (typeof data.seq !== 'number' && typeof existing.seq === 'number') {
    merged.seq = existing.seq
  }
  if (data.parameters && typeof data.parameters === 'object') {
    merged.parameters = { ...existing.parameters, ...data.parameters }
  }
  if (data.args && typeof data.args === 'object') {
    merged.parameters = { ...(merged.parameters || {}), ...data.args }
  }
  // 状态推进统一走状态机（add 模式：显式 status > 事件态映射 > state 未命中兜底 pending）。
  // 终态不回退 / 非法转换回退（如 RUNNING 被旧事件回退为 PENDING）由 applyToolCallState 保证
  //（核心层权威化，Task 2）。
  const { status: nextStatus, applied: statusApplied } = applyToolCallState(existing, data, { mode: 'add' })
  if (statusApplied && nextStatus !== undefined) {
    merged.status = nextStatus
  }
  // 审批数据合并（审批域，与工具执行态解耦；仅附加不推进工具状态）
  if (existing.approval || data.approval) {
    merged.approval = { ...(existing.approval || {}), ...(data.approval || {}) }
  }
  // PENDING + 非终态审批保护（P3-7/P3-13/P3-19/P3-21 根因修复）：
  // SSE tool 事件携带 status='running' 会覆盖 WebSocket approval_pending 设置的 pending 状态，
  // 导致触发浏览器未审批工具显示"执行中"。当工具处于 PENDING 且有非终态审批数据时，
  // 不允许 data.status 覆盖（审批通过后由 approval_approved / tool_call_running 事件推进）。
  // 例外1：目标为终态（completed/failed/timeout）时放行覆盖——终态不可逆，
  // 让快照校对/result 事件中的终态能覆盖本地 pending（修复事件丢失时状态卡在"待审批"）。
  // 例外2：目标为 waiting（tool_call_waiting 审批等待事件）时放行——waiting 是
  // "待审批/审批等待"的合法状态（P3-5 类根因：同批工具因 tool 事件与 approval 事件
  // 到达时序不同，一个停在 pending、一个推进到 waiting，导致状态标签与边框显示不一致）。
  if (existing.status === ToolCallStatus.PENDING
      && existing.approval
      && _NON_TERMINAL_APPROVAL_STATES.includes(existing.approval.state)
      && data.status
      && data.status !== existing.status
      && !isTerminalStatus(data.status)
      && data.status !== ToolCallStatus.WAITING) {
    merged.status = existing.status
  }
  return merged
}

/**
 * 规范化新建 toolCall 的数据（外部字段归一化：args/state → parameters/status）
 *
 * 抽取自 _addOrUpdateToolCallInMessage 的新建逻辑，Map 版本与数组版本共用。
 * 注：args 到 parameters 的转换是入口边界的归一化处理，内部逻辑仅使用 parameters。
 * 状态推导统一走状态机（add 模式）。
 *
 * @param {Object} data - 原始数据（可能含 args、state 等外部字段）
 * @returns {Object} 规范化后的 toolCall 数据
 */
function _normalizeNewToolCall(data) {
  const toolData = { ...data }
  if (!toolData.parameters && toolData.args) {
    toolData.parameters = toolData.args
  }
  // 状态推导统一走状态机（add 模式：显式 status > 事件态映射 > state 未命中兜底 pending，
  // 与后端 stream_chunk_processors._STATE_TO_STATUS 语义一致）
  const { status: nextStatus, applied: statusApplied } = applyToolCallState(null, toolData, { mode: 'add' })
  if (statusApplied && nextStatus !== undefined) {
    toolData.status = nextStatus
  }
  return toolData
}

/**
 * 在 Map 中通过 value 反查 key（isSynthetic key 迁移场景用）
 *
 * @param {Map} map - toolCall Map
 * @param {Object} value - 目标 value
 * @returns {string|undefined} 匹配的 key（未找到时为 undefined）
 */
function _findKeyByValue(map, value) {
  for (const [key, val] of map.entries()) {
    if (val === value) return key
  }
  return undefined
}

/**
 * 在 Map 中查找匹配的 toolCall（审批相关场景）
 *
 * Map 版本的 findToolCallById，匹配策略：
 *   0. Map key 直接匹配 toolCallId
 *   1. data.toolCallId 在 Map key 中查找（altId 查找）
 *   2. 遍历兜底：tc.id / tc.toolCallId / tc.approval 内 interrupt_id、tool_call_id 匹配
 *      toolCallId 或 data.toolCallId（approval 维度匹配用于定位合成占位条目）
 *   3. findToolCallById 兜底（approval 内的 interrupt_id/tool_call_id / toolName 匹配）
 *
 * 合成占位定位（Task 5.3）：审批先到达时创建的占位条目 key=interrupt_id、
 * approval.toolCallId=真实 tool_call_id。真实 tool 事件（id=toolCallId）到达时，
 * 步骤 2 的 approval 维度匹配可命中该占位，从而走删除旧 key 的迁移逻辑，
 * 杜绝"合成 + 真实"并存。
 *
 * @param {Map} toolCallMap - toolCall Map（key 为 toolCallId，value 为 toolCall 对象）
 * @param {string} toolCallId - 待匹配的 ID（通常是 interrupt_id）
 * @param {Object} [data] - 审批数据对象（含 tool_call_id / tool_name 等字段），
 *                          data.toolCallId 用于 altId 查找
 * @returns {{ toolCall: Object|null, key: string|null }} 匹配结果（toolCall 和对应的 Map key）
 */
export function findToolCallInMap(toolCallMap, toolCallId, data = null) {
  if (!toolCallMap || toolCallMap.size === 0 || !toolCallId) {
    return { toolCall: null, key: null }
  }

  // 0. Map key 直接匹配
  if (toolCallMap.has(toolCallId)) {
    return { toolCall: toolCallMap.get(toolCallId), key: toolCallId }
  }

  // 1. data.toolCallId 在 Map key 中查找（altId 查找）
  const altId = data?.toolCallId || data?.approval?.toolCallId
  if (altId && toolCallMap.has(altId)) {
    return { toolCall: toolCallMap.get(altId), key: altId }
  }

  // 2. 遍历兜底：tc.id / tc.toolCallId / tc.approval（interrupt_id、tool_call_id）
  //    匹配 toolCallId 或 data.toolCallId。
  //    approval 维度匹配是合成占位（key=interrupt_id）被真实 tool 事件定位的关键：
  //    即使入参 data 携带 toolCallId（findToolCallById 步骤 0 会短路返回 null），
  //    此处仍能通过占位条目的 approval.toolCallId 命中（Task 5.3）。
  const _matchesApproval = (tc, id) =>
    !!(tc.approval && (tc.approval.interruptId === id || tc.approval.toolCallId === id))
  for (const [key, tc] of toolCallMap.entries()) {
    if (tc.id === toolCallId || tc.toolCallId === toolCallId || _matchesApproval(tc, toolCallId)) {
      return { toolCall: tc, key }
    }
    if (altId && (tc.id === altId || tc.toolCallId === altId || _matchesApproval(tc, altId))) {
      return { toolCall: tc, key }
    }
  }

  // 3. findToolCallById 兜底（approval 内的 interrupt_id/tool_call_id / toolName 匹配）
  const toolCalls = Array.from(toolCallMap.values())
  const approvalData = data?.approval || data
  const toolCall = findToolCallById(toolCalls, toolCallId, {
    approvalData,
    skipApproved: false,
  })
  if (toolCall) {
    const key = _findKeyByValue(toolCallMap, toolCall)
    return { toolCall, key }
  }

  return { toolCall: null, key: null }
}

/**
 * 在 Map 中添加或更新 toolCall（add 路径：工具创建/中间态入口，对应 SSE tool 事件）
 *
 * Map 版本的 _addOrUpdateToolCallInMessage。
 * 合并/保护逻辑与数组版本一致（通过 _mergeExistingToolCall / _normalizeNewToolCall 共用）。
 * 工具结果唯一写入入口为 updateOrAddToolResultInMap（completed/failed/timeout）。
 *
 * key 优先级：tool_call_id > id > oldKey（与 setApprovalToToolCallInMap 创建的占位 key 对齐）
 *
 * 单一幂等入口（Task 5.1）：SSE tool 事件与 WebSocket tool_call_* 事件均通过本函数写入，
 * 以 id/toolCallId 精确匹配（findToolCallInMap）幂等合并，双通道重复推送不产生重复条目。
 *
 * 合成占位迁移（Task 5.3）：
 * - 审批先到达时，setApprovalToToolCallInMap 创建 isSynthetic 占位条目（key=interrupt_id）
 * - 真实 tool 事件到达时，findToolCallInMap 通过 approval.toolCallId 定位占位条目
 * - 合并后清除 isSynthetic 标记，并删除旧 key（interrupt_id），key 迁移到真实 tool_call_id，
 *   杜绝"合成 + 真实"并存
 *
 * @param {Map} toolCallMap - toolCall Map
 * @param {Object} data - 工具事件数据
 * @returns {string|null} toolCallId（成功时）或 null（数据无效/无 ID 时）
 */
export function addOrUpdateToolCallInMap(toolCallMap, data) {
  if (!toolCallMap || !data) return null

  const toolCallId = data.toolCallId || data.id
  // 精确匹配优先（含合成占位定位）：以 id/toolCallId/approval 维度强匹配，
  // 禁止 name/operation 回退匹配（并行同名工具不得互相合并 —— Task 5.2）
  if (toolCallId) {
    const { toolCall: existing, key: oldKey } = findToolCallInMap(toolCallMap, toolCallId, data)
    if (existing) {
      const merged = _mergeExistingToolCall(existing, data)
      // key 优先级：tool_call_id > id > oldKey
      const newKey = merged.toolCallId || merged.id || oldKey
      // 合成占位迁移：清除标记，删除旧 key（interrupt_id），用真实 tool_call_id 作为新 key
      if (merged.isSynthetic && (merged.toolCallId || merged.id)) {
        delete merged.isSynthetic
        if (oldKey && oldKey !== newKey) {
          toolCallMap.delete(oldKey)
        }
      }
      if (newKey) {
        toolCallMap.set(newKey, merged)
      }
      return newKey
    }
  }
  // 新建 toolCall：data 无 id 时不再按 name 回退合并（Task 5.2 —— 并行同名工具
  // 各自独立建条目，语义为"新建待绑定"）；Map 版本新建条目仍需
  // toolCallId/id 作为 key，真实事件（后端 SSE tool / WS tool_call_*）均携带 id。
  const toolData = _normalizeNewToolCall(data)
  const newToolCallId = toolData.toolCallId || toolData.id
  if (!newToolCallId) return null
  toolCallMap.set(newToolCallId, toolData)
  return newToolCallId
}

/**
 * 在 Map 中更新或添加工具结果（result 路径：工具结果唯一写入入口，对应 SSE tool_result / tool_call_completed 等事件）
 *
 * Map 版本的 _updateOrAddToolResultInMessage。
 * 合并/保护逻辑与数组版本一致。
 * 工具创建/中间态入口为 addOrUpdateToolCallInMap（pending/waiting/running）。
 *
 * status 推导优先级（统一走状态机 toolCallStateMachine.applyToolCallState，result 模式）：
 *   1. data.status 显式提供 → 直接使用
 *   2. data.state 在事件态映射表中 → 用映射
 *   3. data.result 有值（且 state 非 output-error）→ COMPLETED
 *   4. data.error 有值 → FAILED
 *
 * 进入终态时设置 completedAt 时间戳。
 *
 * 单一幂等入口（Task 5.1）：SSE tool_result 事件与 WebSocket
 * tool_call_completed/failed/timeout 事件均通过本函数写入，以 id/toolCallId
 * 精确匹配（findToolCallInMap）幂等合并，双通道重复推送不产生重复条目。
 * 命中合成占位（key=interrupt_id）时同步迁移 key 到真实 tool_call_id（Task 5.3）。
 *
 * @param {Map} toolCallMap - toolCall Map
 * @param {Object} data - 工具结果事件数据
 * @returns {string|null} toolCallId（成功时）或 null（数据无效/无 ID 时）
 */
export function updateOrAddToolResultInMap(toolCallMap, data) {
  if (!toolCallMap || !data) return null
  const toolCallId = data.toolCallId || data.id
  if (toolCallId) {
    const { toolCall: existing, key: oldKey } = findToolCallInMap(toolCallMap, toolCallId, data)
    if (existing) {
      const updates = {}
      if (data.state !== undefined) updates.state = data.state
      if (data.result !== undefined) updates.result = data.result
      if (data.error !== undefined) updates.error = data.error
      // 状态推进统一走状态机（result 模式）。终态不回退 / 非法转换回退由
      // applyToolCallState 保证（核心层权威化，Task 2）。
      const { status: nextStatus, applied: statusApplied } = applyToolCallState(existing, data, { mode: 'result' })
      if (statusApplied && nextStatus !== undefined) {
        updates.status = nextStatus
        // 进入终态时清除审批状态（P21/P22 修复），防止审批组件残留
        if (isTerminalStatus(nextStatus) && existing.approval) {
          updates.approval = null
        }
        // 进入终态时设置 completedAt
        if (isTerminalStatus(nextStatus) && !existing.completedAt) {
          updates.completedAt = new Date().toISOString()
        }
      }
      Object.assign(existing, updates)
      // 合成占位迁移：tool_result 先于真实 tool 事件到达时命中占位条目，
      // 清除 isSynthetic 标记并删除旧 key（interrupt_id），key 迁移到真实 tool_call_id
      if (existing.isSynthetic && (existing.toolCallId || existing.id)) {
        delete existing.isSynthetic
        const newKey = existing.toolCallId || existing.id
        if (oldKey && oldKey !== newKey) {
          toolCallMap.delete(oldKey)
        }
        if (newKey) toolCallMap.set(newKey, existing)
      }
      return existing.toolCallId || existing.id
    }
  }
  // 新建（容错场景：tool_result 先于 tool 到达）
  const toolData = _normalizeNewToolCall(data)
  // 状态推导统一走状态机（result 模式）：显式 status > 事件态映射 > result→COMPLETED > error→FAILED；
  // 无字段可推导时保留 _normalizeNewToolCall 的默认值（state→pending）
  const { status: nextStatus, applied: statusApplied } = applyToolCallState(null, toolData, { mode: 'result' })
  if (statusApplied && nextStatus !== undefined) {
    toolData.status = nextStatus
  }
  // 终态时设置 completedAt
  if (toolData.status && isTerminalStatus(toolData.status) && !toolData.completedAt) {
    toolData.completedAt = new Date().toISOString()
  }
  const newToolCallId = toolData.toolCallId || toolData.id
  if (!newToolCallId) return null
  toolCallMap.set(newToolCallId, toolData)
  return newToolCallId
}

/**
 * 将审批数据附加到 Map 中对应 toolCall 的 approval 字段
 *
 * Map 版本的 setApprovalToToolCall（sessionStore）。
 * isSynthetic 占位机制：审批先于真实 tool 事件到达时，创建占位条目使 ToolCallCard 立即渲染审批面板。
 *
 * toolCall 已存在时，仅附加 approval 数据，不修改 status（审批态与工具执行态解耦）。
 * toolCall 不存在时，创建 isSynthetic 占位条目（默认 status=WAITING，与后端
 * tool_call_waiting 事件语义一致：审批先到时占位工具显示"等待中"而非"执行中"）。
 *
 * @param {Map} toolCallMap - toolCall Map
 * @param {string} toolCallId - 工具调用 ID（interrupt_id 或 tool_call_id）
 * @param {Object} approvalData - 审批事件数据
 * @returns {boolean} isSynthetic - 是否创建了 isSynthetic 占位条目（true 时调用方应加入 pendingApprovals 队列）
 */
export function setApprovalToToolCallInMap(toolCallMap, toolCallId, approvalData) {
  if (!toolCallMap || !toolCallId || !approvalData) return false
  // 在 Map values 中查找匹配的 toolCall
  const { toolCall: tc } = findToolCallInMap(toolCallMap, toolCallId, approvalData)
  if (tc) {
    // 工具已进入终态时跳过绑定（核心防护：防止 SnapshotSync 审批校对
    // 在工具完成后重新写入审批数据，导致审批面板残留）
    if (isTerminalStatus(tc.status)) {
      return false
    }
    // toolCall 已存在：仅附加 approval，不修改 status（审批态与工具执行态解耦）
    tc.approval = approvalData
    // seq 补全：审批事件携带的全局递增序号（register 分配，跨浏览器统一排序依据）
    // 写入条目。SSE tool_info 先到达的占位条目（无 seq）据此补全排序依据，
    // 与 tool_call_* 事件透传的 seq 保持一致（同一工具调用排序 key 唯一）
    if (typeof approvalData?.seq === 'number' && typeof tc.seq !== 'number') {
      tc.seq = approvalData.seq
    }
    // P30 修复：tool 事件到达时 parameters 可能为空（{}），但审批数据中有完整参数。
    // 当现有条目 parameters 为空对象时，从审批数据回填，确保工具卡片的"输入参数"正确显示
    const approvalParams = approvalData?.parameters || approvalData?.args
    if (approvalParams && typeof approvalParams === 'object' && Object.keys(approvalParams).length > 0) {
      const existingParams = tc.parameters || {}
      const nonEmptyKeys = Object.keys(existingParams).filter(k => existingParams[k] !== '' && existingParams[k] != null)
      if (nonEmptyKeys.length === 0) {
        tc.parameters = { ...approvalParams }
      }
    }
    return false
  }
  // toolCall 还未到达，创建 isSynthetic 占位条目
  // 顶层 tool_call_id = interrupt_id（作为占位 key，与 Map key 一致）；
  // approval.toolCallId 保留原始 LLM tool_call_id（approvalData.toolCallId），
  // 供真实 tool 事件（id=toolCallId）经 findToolCallInMap 定位并迁移 key（Task 5.3）
  const syntheticToolCall = {
    id: toolCallId,
    toolCallId: toolCallId,
    interruptId: toolCallId,
    name: approvalData?.toolName || 'unknown',
    toolName: approvalData?.toolName || 'unknown',
    parameters: approvalData?.parameters || approvalData?.args || {},
    status: ToolCallStatus.WAITING,
    approval: approvalData,
    isSynthetic: true,
  }
  // 审批事件携带 messageId 时写入 messageBackendId（归属消息定位）：
  // 聊天深度研究模式的审批事件（approval_pending）可能先于 tool_call_waiting 到达，
  // 占位条目提前声明归属，保证 _syncMessageToolCalls 能将其挂载到对应消息
  // （根因修复：工具事件 messageId 缺失时审批占位仍是消息内可见的工具调用）
  if (approvalData?.messageId) {
    syntheticToolCall.messageBackendId = String(approvalData.messageId)
  }
  // 审批事件携带 seq 时写入（跨浏览器统一排序依据）：审批占位（isSynthetic）
  // 在真实 tool 事件到达前即可获得与工具事件一致的排序 key
  if (typeof approvalData?.seq === 'number') {
    syntheticToolCall.seq = approvalData.seq
  }
  const op = approvalData?.operation || approvalData?.command
  if (op) {
    syntheticToolCall.parameters.command = op
  }
  toolCallMap.set(toolCallId, syntheticToolCall)
  return true
}

/**
 * 刷新待绑定的审批数据（session.js 与 research.js 共享）
 *
 * 在 addOrUpdateToolCall / updateOrAddToolResult 创建/更新 toolCall 后调用，
 * 处理审批事件先于 tool 事件到达的时序场景：
 * 1. 从 pendingApprovals Map 中查找匹配的审批
 * 2. 从队列移除后调用 setApprovalToToolCallInMap 绑定（此时 toolCallMap 中已有目标条目）
 *
 * @param {Map} pendingMap - pendingApprovals Map (key=toolCallId, value={approvalData, toolCallId})
 * @param {Map} toolCallMap - toolCall Map
 * @param {string} toolCallId - 工具调用 ID
 * @returns {boolean} 是否执行了绑定
 */
export function flushPendingApprovalsInMap(pendingMap, toolCallMap, toolCallId) {
  if (!pendingMap || !toolCallMap || !toolCallId) return false
  const pending = pendingMap.get(toolCallId)
  if (!pending) return false

  // 工具已进入终态时跳过绑定：updateOrAddToolResultInMap 已将 approval 置 null，
  // 此时 pending 中的审批数据是旧批次的残留（已执行完毕），重新绑定会导致审批面板
  // 在"已完成"工具上永久显示。这是根因防护，覆盖所有调用方。
  const existingTc = toolCallMap.get(toolCallId)
  if (existingTc) {
    if (isTerminalStatus(existingTc.status)) {
      pendingMap.delete(toolCallId)
      return false
    }
  }

  pendingMap.delete(toolCallId)
  // 同时移除 altId（如 approvalData.toolCallId）对应的条目
  if (pending.approvalData?.toolCallId && pending.approvalData.toolCallId !== toolCallId) {
    pendingMap.delete(pending.approvalData.toolCallId)
  }

  setApprovalToToolCallInMap(toolCallMap, toolCallId, pending.approvalData)
  return true
}

/**
 * 在 Map 中更新 toolCall 的 approval.state
 *
 * Map 版本的 updateToolCallApprovalStateOnly（sessionStore / researchStore 共用）。
 * 审批态与工具执行态解耦：approval.state 驱动审批面板显示/按钮禁用，
 * toolCall.status 由 tool_result 事件驱动（completed/failed）。
 *
 * 本函数仅更新 approval.state，不修改 toolCall.status。
 * 若需同时同步 status（如审批通过后流转到 running/completed），调用方应显式调用
 * updateToolCallStatusInMap。
 *
 * @param {Map} toolCallMap - toolCall Map
 * @param {string} toolCallId - 工具调用 ID
 * @param {string} state - 新的 approval.state（如 'processing' / 'approved' / 'rejected' / 'timeout'）
 * @param {Object} [data] - 审批数据（用于辅助查找 toolCall）
 * @returns {boolean} 是否成功更新
 */
export function updateApprovalStateInMap(toolCallMap, toolCallId, state, data = null) {
  if (!toolCallMap || !toolCallId) return false
  const { toolCall: tc } = findToolCallInMap(toolCallMap, toolCallId, data)
  if (!tc) return false
  // 工具已进入终态时禁止创建/修改 approval：
  // approval_processed 事件可能在 tool_result 之后到达（WebSocket 事件乱序），
  // 此时重新创建 tc.approval 会导致已完成的工具上审批面板永久残留
  if (isTerminalStatus(tc.status)) return false
  // approval 不存在时初始化为空对象后设置 state
  if (!tc.approval) tc.approval = {}
  tc.approval.state = state
  return true
}

/**
 * 在 Map 中更新 toolCall 的 status
 *
 * Map 版本的 updateToolCallStatus（sessionStore）。
 * 用于深度研究审批通过后设置 running 状态等场景。
 *
 * @param {Map} toolCallMap - toolCall Map
 * @param {string} toolCallId - 工具调用 ID
 * @param {string} status - 新的 toolCall.status
 * @param {Object} [data] - 审批数据（用于辅助查找 toolCall）
 * @returns {boolean} 是否成功更新（toolCall 不存在时返回 false）
 */
export function updateToolCallStatusInMap(toolCallMap, toolCallId, status, data = null) {
  if (!toolCallMap || !toolCallId) return false
  const { toolCall: tc } = findToolCallInMap(toolCallMap, toolCallId, data)
  if (!tc) return false
  // 状态推进统一走状态机（add 模式）：终态不回退 / 非法转换回退由 applyToolCallState 保证
  const { status: nextStatus, applied: statusApplied } = applyToolCallState(tc, { status }, { mode: 'add' })
  if (!statusApplied || nextStatus === undefined) return false
  tc.status = nextStatus
  return true
}

/**
 * 最终化 toolCallsMap 中指定消息的所有非终态 toolCalls
 *
 * 用于流式结束时兜底（messageIntegrity.finalizeToolCallsForCompletedMessage 调用）：
 * 将卡在 pending/running/waiting 的 toolCall.status 修正为 COMPLETED，
 * 将卡在 pending/processing/waiting 的 approval.state 修正为 TIMEOUT。
 *
 * 设计原因：WebSocket tool_call_completed / approval_approved 事件可能丢失或乱序，
 * 流式已结束说明 agent 处理完毕，所有非终态工具调用应兜底为终态，
 * 避免 UI 永久显示"执行中"或审批按钮不消失（P3-22/P3-23 根因）。
 *
 * 与 messageIntegrity.finalizeToolCallsForCompletedMessage 的分工：
 *   - messageIntegrity 直接修改 message.toolCalls 数组（UI 派生数据）
 *   - 本函数修改 toolCallsMap（单一真相源），确保 Map → message.toolCalls 单向数据流一致
 *
 * @param {Map<string, Object>} toolCallMap - toolCallsMap（按 toolCallId 索引）
 * @param {string} [messageBackendId] - 可选，仅最终化属于该消息的 toolCalls；
 *   不传则最终化该 session 下所有 toolCalls
 * @returns {number} 最终化的 toolCall 数量（用于日志可观测性）
 */
export function finalizeToolCallsInMap(toolCallMap, messageBackendId) {
  if (!toolCallMap || toolCallMap.size === 0) return 0
  let finalizedCount = 0
  const targetId = messageBackendId != null ? String(messageBackendId) : null
  for (const tc of toolCallMap.values()) {
    if (!tc) continue
    // 未指定 messageBackendId 时最终化全部；指定时仅匹配该消息
    if (targetId) {
      const tcMsgId = tc.messageBackendId != null ? String(tc.messageBackendId) : null
      if (tcMsgId !== targetId) continue
    }
    // 审批未放行执行（state 非 approved/completed）：工具尚未被确认执行，
    // 流式结束不代表其已完成，不得兜底为 COMPLETED（P3-34/P3-35 根因修复）。
    // 覆盖 pending/processing/waiting（审批未完成）与 rejected/timeout（未执行）；
    // 审批超时/拒绝由后端 tool_call_timeout / tool_call_rejected 事件驱动，前端不代为判定。
    if (tc.approval
        && tc.approval.state !== 'approved'
        && tc.approval.state !== 'completed') {
      continue
    }
    let changed = false
    // 工具状态非终态 → COMPLETED（流式已结束，工具必然已完成）。
    // 状态推进统一走状态机（add 模式），终态不回退由 applyToolCallState 保证
    if (NON_TERMINAL_STATUSES.has(tc.status)) {
      const { status: nextStatus, applied: statusApplied } =
        applyToolCallState(tc, { status: ToolCallStatus.COMPLETED }, { mode: 'add' })
      if (statusApplied && nextStatus !== undefined) {
        tc.status = nextStatus
        if (!tc.state) tc.state = 'output-available'
        changed = true
      }
    }
    if (changed) finalizedCount++
  }
  return finalizedCount
}

/**
 * 工具调用显示排序（跨浏览器统一排序的唯一权威，根因修复）
 *
 * 排序 key 优先级：
 *   1. seq：后端 (module, module_id) 内跨 LLM 轮次全局递增序号（register 分配、
 *      事件透传），跨轮唯一。不同浏览器对同一批工具调用据此排序完全一致
 *      （修复跨浏览器顺序不一致：原 _index 为 LLM 单轮序号跨轮重复，相等时
 *      退化为各浏览器本地 Map 插入顺序，双轨事件浏览器插入时机不同导致错序）。
 *   2. _index：LLM 单轮输出内序号，仅作同 seq 时兜底（同轮多工具顺序稳定）。
 *   3. 两者皆无：保持原数组顺序（JS 稳定排序，即 Map 插入顺序兜底）。
 *
 * @param {Array} arr - 工具调用数组（原地排序）
 * @returns {Array} 排序后的数组
 */
export function sortToolCallsForDisplay(arr) {
  if (!Array.isArray(arr) || arr.length <= 1) return arr
  arr.sort((a, b) => {
    const as = typeof a?.seq === 'number' ? a.seq : Number.MAX_SAFE_INTEGER
    const bs = typeof b?.seq === 'number' ? b.seq : Number.MAX_SAFE_INTEGER
    if (as !== bs) return as - bs
    const ai = a?._index ?? 999
    const bi = b?._index ?? 999
    return ai - bi
  })
  return arr
}

/**
 * 合并本地 toolCalls 与后端 toolCalls（增量合并，保留本地更完整的数据）
 *
 * 用于 loadHistory 与快照校对场景：刷新后从后端拉取 toolCalls 历史，
 * 与本地实时同步数据合并（所有模块刷新时统一通过合并而非替换）。
 *
 * 合并规则：
 * - 按 id / tool_call_id 去重
 * - 后端数据为准（含 result / status），但保留本地更完整的状态：
 *   1. 本地 result 有值而后端为空时，保留本地 result
 *   2. 本地为终态、后端为非终态时，保留本地终态（避免状态回退）
 *   3. 本地审批中间状态（pending/processing/waiting）始终保留
 * - 本地 isSynthetic 占位条目若已被真实 toolCall 替换，则丢弃
 * - 本地重复条目坍缩：同 toolCallId / 同 id 的多条本地条目（如合成占位未被清理时
 *   与真实条目并存，同 toolCallId 不同 id）只保留一条（Task 5.3）
 *
 * @param {Array} existingList - 本地 toolCalls 数组
 * @param {Array} backendList - 后端 toolCalls 数组
 * @returns {Array} 合并后的 toolCalls 数组
 */
export function _mergeToolCalls(existingList, backendList) {
  if (!Array.isArray(existingList) || existingList.length === 0) {
    return Array.isArray(backendList) ? backendList : []
  }
  if (!Array.isArray(backendList) || backendList.length === 0) {
    return existingList
  }
  const merged = []
  const localOnly = []
  const usedBackendIds = new Set()
  // 已输出条目的 id/toolCallId 集合：用于本地重复条目坍缩（Task 5.3）
  const outputIds = new Set()
  const _recordOutput = (tc) => {
    if (tc.id) outputIds.add(tc.id)
    if (tc.toolCallId) outputIds.add(tc.toolCallId)
  }
  const _isDuplicateOutput = (tc) =>
    (!!tc.id && outputIds.has(tc.id)) || (!!tc.toolCallId && outputIds.has(tc.toolCallId))
  // 第一遍：遍历本地，匹配后端（后端匹配条目优先输出，作为重复坍缩的保留基准）
  for (const local of existingList) {
    const localId = local.id || local.toolCallId
    const backend = backendList.find(b => {
      const bId = b.id || b.toolCallId
      return bId && bId === localId
    })
    if (backend) {
      // 合并：后端数据为主，但保留本地更完整/更超前的状态
      const mergedTc = { ...local, ...backend }
      // 保留本地 result（后端为空/null 时，避免丢失本地已有的结果）
      if (local.result != null && backend.result == null) {
        mergedTc.result = local.result
      }
      // 保留本地终态 status（避免本地已完成却被后端旧快照回退为 running）
      if (local.status && TERMINAL_STATUSES.has(local.status)
        && backend.status && !TERMINAL_STATUSES.has(backend.status)) {
        mergedTc.status = local.status
      }
      // 保留本地审批状态（后端可能滞后）
      if (local.approval && backend.approval) {
        mergedTc.approval = { ...local.approval, ...backend.approval }
      } else if (local.approval) {
        mergedTc.approval = local.approval
      }
      // 本地重复坍缩：同 id/toolCallId 已输出则跳过（Task 5.3）
      if (_isDuplicateOutput(mergedTc)) continue
      merged.push(mergedTc)
      usedBackendIds.add(backend.id || backend.toolCallId)
      _recordOutput(mergedTc)
    } else {
      localOnly.push(local)
    }
  }
  // 第二遍：追加本地独有。
  // - isSynthetic 占位若后端无对应，丢弃（已被真实 toolCall 替代或不再需要）
  // - 与已输出条目重复（同 id/toolCallId 不同 id）坍缩为一条（Task 5.3）
  for (const local of localOnly) {
    if (local.isSynthetic) continue
    if (_isDuplicateOutput(local)) continue
    merged.push(local)
    _recordOutput(local)
  }
  // 第三遍：追加后端独有
  for (const backend of backendList) {
    const bId = backend.id || backend.toolCallId
    if (bId && !usedBackendIds.has(bId)) {
      // 与已输出条目重复（同 id/toolCallId）时跳过，保持坍缩一致性
      if (_isDuplicateOutput(backend)) continue
      merged.push(backend)
      _recordOutput(backend)
    }
  }
  return merged
}

/**
 * 合并本地消息与后端快照消息（快照校对场景）
 *
 * 用于 useSnapshotSync：从后端拉取快照后，将快照消息合并到本地，
 * 而非粗暴替换。保护本地流式期间更完整/更新的数据。
 *
 * 保护态（PROTECTED_STREAM_STATES）下的合并规则：
 * - content：本地为空或后端更长时允许覆盖 mapped.content；
 *   versions[current].content 始终保留本地（保护态下不更新版本内容）
 * - toolCalls：后端数量 >= 本地时合并（_mergeToolCalls 保留本地更完整的 result/status）；
 *   后端数量 < 本地时保留本地（快照滞后，本地更完整）
 * - reasoning：后端 reasoning.content 更长时允许覆盖
 * - sources/suggestions/context：保留本地（流式期间本地更完整）
 * - 非内容字段（tokenCount/responseTime/model/backendId）：始终以后端为准更新
 *
 * 非保护态下：后端为准覆盖所有字段。
 *
 * @param {Object} existingMsg - 本地消息
 * @param {Object} backendMsg - 后端快照消息（已通过 transformBackendMessageToFrontend 转换）
 * @returns {Object} 合并后的消息（变异 existingMsg 并返回其引用）
 */
export function mergeMessageFromBackend(existingMsg, backendMsg) {
  if (!existingMsg) return backendMsg
  if (!backendMsg) return existingMsg

  const isProtected = PROTECTED_STREAM_STATES.has(existingMsg.streamState)
  const _NON_CONTENT_FIELDS = ['tokenCount', 'responseTime', 'model', 'backendId', 'researchTaskId', 'researchTaskStatus']

  // 非内容字段始终以后端为准（后端是元数据权威）
  for (const field of _NON_CONTENT_FIELDS) {
    if (backendMsg[field] !== undefined) {
      existingMsg[field] = backendMsg[field]
    }
  }

  if (isProtected) {
    // content 保护：本地为空或后端更长时允许覆盖
    const localContent = existingMsg.content || ''
    const backendContent = backendMsg.content || ''
    const localContentEmpty = localContent.length === 0
    const backendLonger = backendContent.length > localContent.length
    if (localContentEmpty || backendLonger) {
      if (backendMsg.content !== undefined) existingMsg.content = backendMsg.content
    }
    // 否则保留本地 content

    // toolCalls 保护：后端数量 >= 本地时合并（保留本地更完整状态）；否则保留本地
    const localToolCalls = existingMsg.toolCalls || []
    const backendToolCalls = Array.isArray(backendMsg.toolCalls) ? backendMsg.toolCalls : []
    if (backendToolCalls.length >= localToolCalls.length) {
      existingMsg.toolCalls = _mergeToolCalls(localToolCalls, backendToolCalls)
    }
    // 否则保留本地 toolCalls

    // reasoning 保护：后端 reasoning.content 更长时允许覆盖
    if (backendMsg.reasoning !== undefined) {
      const localReasoningContent = existingMsg.reasoning?.content || ''
      const backendReasoningContent = backendMsg.reasoning?.content || ''
      if (backendReasoningContent.length > localReasoningContent.length) {
        existingMsg.reasoning = backendMsg.reasoning
      }
      // 否则保留本地
    }

    // sources/suggestions/context：保留本地（流式期间本地更完整）
  } else {
    // 非保护态：后端为准覆盖所有字段
    if (backendMsg.content !== undefined) existingMsg.content = backendMsg.content
    if (Array.isArray(backendMsg.toolCalls)) {
      existingMsg.toolCalls = _mergeToolCalls(existingMsg.toolCalls || [], backendMsg.toolCalls)
    }
    if (backendMsg.reasoning !== undefined) existingMsg.reasoning = backendMsg.reasoning
    if (backendMsg.sources !== undefined) existingMsg.sources = backendMsg.sources
    if (backendMsg.suggestions !== undefined) existingMsg.suggestions = backendMsg.suggestions
    if (backendMsg.context !== undefined) existingMsg.context = backendMsg.context
  }

  // versions 同步：将合并结果同步到当前版本快照
  const versionIdx = existingMsg.currentVersion
  if (existingMsg.versions && versionIdx !== undefined && existingMsg.versions[versionIdx]) {
    const ver = existingMsg.versions[versionIdx]
    for (const field of _NON_CONTENT_FIELDS) {
      if (existingMsg[field] !== undefined) ver[field] = existingMsg[field]
    }
    ver.toolCalls = existingMsg.toolCalls
    ver.sources = existingMsg.sources
    ver.reasoning = existingMsg.reasoning
    ver.suggestions = existingMsg.suggestions
    ver.context = existingMsg.context
    // 保护态下 versions[current].content 保留本地；非保护态下同步 content
    if (!isProtected && existingMsg.content !== undefined) {
      ver.content = existingMsg.content
    }
  }

  return existingMsg
}

/**
 * 判断参数对象是否为非空纯对象
 *
 * 用于判断 toolCall 的 parameters/args 是否有实际内容，
 * 决定是否显示参数展示区域。
 *
 * @param {*} params - 待检查的参数
 * @returns {boolean} true 表示是非空纯对象（非 null、非数组、至少一个自有可枚举属性）
 */
export function isNonEmptyParams(params) {
  if (!params || typeof params !== 'object' || Array.isArray(params)) return false
  return Object.keys(params).length > 0
}

