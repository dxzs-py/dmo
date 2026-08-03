import { PROTECTED_STATUSES, PROTECTED_STREAM_STATES, ToolCallStatus, mapApprovalStateToStatus } from '../types'

// ==================== 标准化常量（统一所有模块的工具调用状态判断） ====================

/** 工具调用的终态状态集合（完成后不可逆） */
const _TERMINAL_TOOL_STATUSES_SET = new Set([
  ToolCallStatus.COMPLETED,
  ToolCallStatus.FAILED,
  ToolCallStatus.TIMEOUT,
  ToolCallStatus.REJECTED,
])

/**
 * 标准化状态流转检查：审批态向执行终态的流转是否允许
 *
 * 审批态的 toolCall（approved/processing/waiting）在收到工具结果时，
 * 应允许状态向 running/completed/failed 正常流转。
 * 这是 4 处重复逻辑的统一版本，所有模块通过此函数共享同一套规则。
 *
 * @param {string} existingStatus - 当前 toolCall 的 status
 * @param {string} incomingStatus - SSE/WebSocket 事件携带的 status
 * @returns {boolean} 是否允许状态流转
 */
function _isApprovedTransition(existingStatus, incomingStatus) {
  const allowedSources = [ToolCallStatus.APPROVED, ToolCallStatus.PROCESSING, ToolCallStatus.WAITING]
  const allowedTargets = [ToolCallStatus.RUNNING, ToolCallStatus.COMPLETED, ToolCallStatus.FAILED]
  return allowedSources.includes(existingStatus) && allowedTargets.includes(incomingStatus)
}

/**
 * 标准化终态判断（供 session.js 和 research.js 的 isTerminal 检查复用）
 * @param {string} status
 * @returns {boolean}
 */
export const isTerminalStatus = (status) => _TERMINAL_TOOL_STATUSES_SET.has(status)

function _paramsOverlap(a, b) {
  for (const key of Object.keys(a)) {
    if (key in b && String(a[key]) === String(b[key])) return true
  }
  return false
}

function _findMatchingToolCall(toolCalls, data) {
  if (!toolCalls || toolCalls.length === 0) return -1
  if (data.id) {
    const idx = toolCalls.findIndex(t => t.id === data.id)
    if (idx >= 0) return idx
    if (data.name) {
      const nameIdx = toolCalls.findIndex(t => t.name === data.name && !t.id && !t.result)
      if (nameIdx >= 0) return nameIdx
    }
    return -1
  }
  if (data.name) {
    const dataParams = data.parameters || data.args
    if (dataParams && typeof dataParams === 'object') {
      for (let i = toolCalls.length - 1; i >= 0; i--) {
        const t = toolCalls[i]
        if (t.name !== data.name || t.result) continue
        const tParams = t.parameters || t.args
        if (tParams && typeof tParams === 'object' && _paramsOverlap(dataParams, tParams)) {
          return i
        }
      }
    }
    for (let i = toolCalls.length - 1; i >= 0; i--) {
      if (toolCalls[i].name === data.name && !toolCalls[i].result) return i
    }
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
  const _getToolName = (t) => t.name || t.tool_name || t.function?.name
  /** 判断 toolCall 是否处于审批终态（已确认/已拒绝/已超时/已完成） */
  const _isApprovalFinal = (t) => {
    if (!t.approval) return false
    const finalStates = ['approved', 'rejected', 'timeout', 'completed']
    return finalStates.includes(t.approval.state)
  }

  // 0. tool_call_id 精确匹配（最可靠，匹配 t.id 和 t.tool_call_id 两个维度）
  if (approvalData?.tool_call_id) {
    const tcId = approvalData.tool_call_id
    const tc = toolCalls.find(t => t.id === tcId || t.tool_call_id === tcId)
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
    t.approval && (t.approval.interrupt_id === toolCallId || t.approval.tool_call_id === toolCallId)
  )
  if (tc) return tc
  // 2. toolName + operation 内容匹配
  if (approvalData?.tool_name) {
    const _op = approvalData.operation || approvalData.command
    if (_op) {
      tc = toolCalls.find(t =>
        _getToolName(t) === approvalData.tool_name &&
        (_getCmd(t) === _op || Object.values(t.parameters || {}).some(v => String(v) === _op)) &&
        (skipApproved ? !_isApprovalFinal(t) : true)
      )
      if (tc) return tc
    }
    // 3. toolName 宽松匹配
    for (let i = toolCalls.length - 1; i >= 0; i--) {
      const t = toolCalls[i]
      if (_getToolName(t) === approvalData.tool_name && (skipApproved ? !_isApprovalFinal(t) : true)) {
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
  return approval?.interrupt_id || approval?.tool_call_id || ''
}

function _addOrUpdateToolCallInMessage(message, data) {
  if (!message.toolCalls) message.toolCalls = []
  const idx = _findMatchingToolCall(message.toolCalls, data)
  if (idx >= 0) {
    const existing = message.toolCalls[idx]
    const merged = { ...existing, ...data }
    if (data.parameters && typeof data.parameters === 'object') {
      merged.parameters = { ...existing.parameters, ...data.parameters }
    }
    if (data.args && typeof data.args === 'object') {
      merged.parameters = { ...(merged.parameters || {}), ...data.args }
    }
    // 保护审批相关状态不被 SSE 流中的 status/state 覆盖，
    // 但允许审批态向执行终态正常流转（统一使用 _isApprovedTransition）
    if (PROTECTED_STATUSES.includes(existing.status) && existing.approval) {
      if (!_isApprovedTransition(existing.status, data.status)) {
        merged.status = existing.status
      }
      merged.approval = { ...existing.approval, ...(data.approval || {}) }
    } else if (!merged.status && merged.state) {
      const stateMap = { 'input-available': 'running', 'output-available': 'completed', 'output-error': 'failed' }
      merged.status = stateMap[merged.state] || 'running'
    }
    message.toolCalls[idx] = merged
  } else {
    const toolData = { ...data }
    if (!toolData.parameters && toolData.args) {
      toolData.parameters = toolData.args
    }
    if (!toolData.status && toolData.state) {
      const stateMap = { 'input-available': 'running', 'output-available': 'completed', 'output-error': 'failed' }
      toolData.status = stateMap[toolData.state] || 'running'
    }
    message.toolCalls.push(toolData)
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
    // 保护审批相关状态：使用统一的 _isApprovedTransition 和 isTerminalStatus
    if (PROTECTED_STATUSES.includes(existing.status) && existing.approval) {
      if (_isApprovedTransition(existing.status, data.status)) {
        if (data.status !== undefined) updates.status = data.status
        // 工具进入终态后清除审批状态，防止审批组件残留
        if (isTerminalStatus(updates.status)) {
          updates.approval = null
        }
      }
    } else {
      // status 推导：显式 status > stateMap > result→COMPLETED > error→FAILED
      if (data.status !== undefined) {
        updates.status = data.status
      } else if (data.state !== undefined) {
        const stateMap = { 'input-available': 'running', 'output-available': 'completed', 'output-error': 'failed' }
        updates.status = stateMap[data.state]
      }
      if (!updates.status && data.result != null && data.state !== 'output-error') {
        updates.status = ToolCallStatus.COMPLETED
      }
      if (!updates.status && data.error != null) {
        updates.status = ToolCallStatus.FAILED
      }
      // P21/P22 修复：非保护状态下进入终态也清除审批状态
      if (updates.status && isTerminalStatus(updates.status) && existing.approval) {
        updates.approval = null
      }
    }
    // 进入终态时设置 completed_at（Task 16 P1 修复）
    if (updates.status && isTerminalStatus(updates.status) && !existing.completed_at) {
      updates.completed_at = new Date().toISOString()
    }
    Object.assign(message.toolCalls[idx], updates)
  } else {
    const toolData = { ...data }
    // status 推导：显式 status > stateMap > result→COMPLETED > error→FAILED（Task 16 P1 修复）
    const stateMap = { 'input-available': 'running', 'output-available': 'completed', 'output-error': 'failed' }
    if (data.status !== undefined) {
      toolData.status = data.status
    } else if (data.state && stateMap[data.state]) {
      toolData.status = stateMap[data.state]
    } else if (data.result != null && data.state !== 'output-error') {
      toolData.status = ToolCallStatus.COMPLETED
    } else if (data.error != null) {
      toolData.status = ToolCallStatus.FAILED
    } else if (!toolData.status) {
      toolData.status = toolData.state ? (stateMap[toolData.state] || 'running') : 'running'
    }
    // 终态时设置 completed_at
    if (toolData.status && isTerminalStatus(toolData.status) && !toolData.completed_at) {
      toolData.completed_at = new Date().toISOString()
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
      if (approvalData?.state) {
        matched.status = mapApprovalStateToStatus(approvalData.state)
      }
      // 匹配成功后，移除对应的合成 toolCall（避免重复渲染）
      if (matched._synthetic !== true) {
        const syntheticIdx = message.toolCalls.findIndex(
          t => t._synthetic === true && t.id === toolCallId
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

export function addOrUpdateToolCallInLastMessage(sessions, sessionId, data) {
  const result = getLastAssistantMessage(sessions, sessionId)
  if (!result) return
  _addOrUpdateToolCallInMessage(result.message, data)
  const ver = result.message.versions?.[result.message.currentVersion]
  if (ver) _addOrUpdateToolCallInMessage(ver, data)

  _matchPendingApprovals(result.message, data)
  if (ver) _matchPendingApprovals(ver, data)
}

export function updateOrAddToolResultInLastMessage(sessions, sessionId, data) {
  const result = getLastAssistantMessage(sessions, sessionId)
  if (!result) return
  _updateOrAddToolResultInMessage(result.message, data)
  const ver = result.message.versions?.[result.message.currentVersion]
  if (ver) _updateOrAddToolResultInMessage(ver, data)
}

export function addOrUpdateToolCallInMessageByIdx(sessions, sessionId, messageIndex, data) {
  const result = getMessageByIndex(sessions, sessionId, messageIndex)
  if (!result) return
  _addOrUpdateToolCallInMessage(result.message, data)
  const ver = result.message.versions?.[result.message.currentVersion]
  if (ver) _addOrUpdateToolCallInMessage(ver, data)
}

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
  if (!result) return
  result.message[field] = value
  const ver = result.message.versions?.[result.message.currentVersion]
  if (ver) ver[field] = value
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
// Map 的 key 为 toolCall.id || toolCall.tool_call_id。
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
  if (data.parameters && typeof data.parameters === 'object') {
    merged.parameters = { ...existing.parameters, ...data.parameters }
  }
  if (data.args && typeof data.args === 'object') {
    merged.parameters = { ...(merged.parameters || {}), ...data.args }
  }
  // 保护审批相关状态（与 _addOrUpdateToolCallInMessage 共用统一的 _isApprovedTransition）
  if (PROTECTED_STATUSES.includes(existing.status) && existing.approval) {
    if (!_isApprovedTransition(existing.status, data.status)) {
      merged.status = existing.status
    }
    merged.approval = { ...existing.approval, ...(data.approval || {}) }
  } else if (!merged.status && merged.state) {
    const stateMap = { 'input-available': 'running', 'output-available': 'completed', 'output-error': 'failed' }
    merged.status = stateMap[merged.state] || 'running'
  }
  return merged
}

/**
 * 规范化新建 toolCall 的数据（外部字段归一化：args/state → parameters/status）
 *
 * 抽取自 _addOrUpdateToolCallInMessage 的新建逻辑，Map 版本与数组版本共用。
 * 注：args 到 parameters 的转换是入口边界的归一化处理，内部逻辑仅使用 parameters。
 *
 * @param {Object} data - 原始数据（可能含 args、state 等外部字段）
 * @returns {Object} 规范化后的 toolCall 数据
 */
function _normalizeNewToolCall(data) {
  const toolData = { ...data }
  if (!toolData.parameters && toolData.args) {
    toolData.parameters = toolData.args
  }
  if (!toolData.status && toolData.state) {
    const stateMap = { 'input-available': 'running', 'output-available': 'completed', 'output-error': 'failed' }
    toolData.status = stateMap[toolData.state] || 'running'
  }
  return toolData
}

/**
 * 在 Map 中通过 value 反查 key（_synthetic key 迁移场景用）
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
 *   1. data.tool_call_id 在 Map key 中查找（altId 查找）
 *   2. 遍历兜底：tc.id / tc.tool_call_id 匹配 toolCallId 或 data.tool_call_id
 *   3. findToolCallById 兜底（approval 内的 interrupt_id/tool_call_id / toolName 匹配）
 *
 * @param {Map} toolCallMap - toolCall Map（key 为 toolCallId，value 为 toolCall 对象）
 * @param {string} toolCallId - 待匹配的 ID（通常是 interrupt_id）
 * @param {Object} [data] - 审批数据对象（含 tool_call_id / tool_name 等字段），
 *                          data.tool_call_id 用于 altId 查找
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

  // 1. data.tool_call_id 在 Map key 中查找（altId 查找）
  const altId = data?.tool_call_id || data?.approval?.tool_call_id
  if (altId && toolCallMap.has(altId)) {
    return { toolCall: toolCallMap.get(altId), key: altId }
  }

  // 2. 遍历兜底：tc.id / tc.tool_call_id 匹配 toolCallId 或 data.tool_call_id
  for (const [key, tc] of toolCallMap.entries()) {
    if (tc.id === toolCallId || tc.tool_call_id === toolCallId) {
      return { toolCall: tc, key }
    }
    if (altId && (tc.id === altId || tc.tool_call_id === altId)) {
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
 * 在 Map 中添加或更新 toolCall（对应 SSE tool 事件）
 *
 * Map 版本的 _addOrUpdateToolCallInMessage。
 * 合并/保护逻辑与数组版本一致（通过 _mergeExistingToolCall / _normalizeNewToolCall 共用）。
 *
 * key 优先级：tool_call_id > id（与 setApprovalToToolCallInMap 创建的占位 key 对齐）
 *
 * _synthetic key 迁移场景：
 * - 审批先到达时，setApprovalToToolCallInMap 创建 _synthetic 占位条目（key=interrupt_id）
 * - 真实 tool 事件到达时，_findMatchingToolCall 通过 name 匹配到 _synthetic 条目
 * - 合并后清除 _synthetic 标记，并将 key 从 interrupt_id 迁移到真实 tool_call_id
 *
 * @param {Map} toolCallMap - toolCall Map
 * @param {Object} data - 工具事件数据
 * @returns {string|null} toolCallId（成功时）或 null（数据无效/无 ID 时）
 */
export function addOrUpdateToolCallInMap(toolCallMap, data) {
  if (!toolCallMap || !data) return null
  const toolCalls = Array.from(toolCallMap.values())
  const idx = _findMatchingToolCall(toolCalls, data)
  if (idx >= 0) {
    const existing = toolCalls[idx]
    const merged = _mergeExistingToolCall(existing, data)
    // key 优先级：tool_call_id > id > oldKey
    const oldKey = _findKeyByValue(toolCallMap, existing)
    const newKey = merged.tool_call_id || merged.id || oldKey
    // _synthetic 占位迁移：清除标记，删除旧 key，用真实 tool_call_id 作为新 key
    if (merged._synthetic && (merged.tool_call_id || merged.id)) {
      delete merged._synthetic
      if (oldKey && oldKey !== newKey) {
        toolCallMap.delete(oldKey)
      }
    }
    if (newKey) {
      toolCallMap.set(newKey, merged)
    }
    return newKey
  }
  // 新建 toolCall
  const toolData = _normalizeNewToolCall(data)
  const toolCallId = toolData.tool_call_id || toolData.id
  if (!toolCallId) return null
  toolCallMap.set(toolCallId, toolData)
  return toolCallId
}

/**
 * 在 Map 中更新或添加工具结果（对应 SSE tool_result 事件）
 *
 * Map 版本的 _updateOrAddToolResultInMessage。
 * 合并/保护逻辑与数组版本一致。
 *
 * status 推导优先级：
 *   1. data.status 显式提供 → 直接使用
 *   2. data.state 在 stateMap 中 → 用 stateMap 映射
 *   3. data.result 有值（且 state 非 output-error）→ COMPLETED
 *   4. data.error 有值 → FAILED
 *
 * 进入终态时设置 completed_at 时间戳。
 *
 * @param {Map} toolCallMap - toolCall Map
 * @param {Object} data - 工具结果事件数据
 * @returns {string|null} toolCallId（成功时）或 null（数据无效/无 ID 时）
 */
export function updateOrAddToolResultInMap(toolCallMap, data) {
  if (!toolCallMap || !data) return null
  const toolCalls = Array.from(toolCallMap.values())
  const idx = _findMatchingToolCall(toolCalls, data)
  if (idx >= 0) {
    const existing = toolCalls[idx]
    const updates = {}
    if (data.state !== undefined) updates.state = data.state
    if (data.result !== undefined) updates.result = data.result
    if (data.error !== undefined) updates.error = data.error
    // 保护审批相关状态（统一使用 _isApprovedTransition 和 isTerminalStatus）
    if (PROTECTED_STATUSES.includes(existing.status) && existing.approval) {
      if (_isApprovedTransition(existing.status, data.status)) {
        if (data.status !== undefined) updates.status = data.status
        // 工具从审批态进入终态后清除审批状态
        if (isTerminalStatus(updates.status)) {
          updates.approval = null
        }
      }
    } else {
      // status 推导：显式 status > stateMap > result→COMPLETED > error→FAILED
      if (data.status !== undefined) {
        updates.status = data.status
      } else if (data.state !== undefined) {
        const stateMap = { 'input-available': 'running', 'output-available': 'completed', 'output-error': 'failed' }
        updates.status = stateMap[data.state]
      }
      if (!updates.status && data.result != null && data.state !== 'output-error') {
        updates.status = ToolCallStatus.COMPLETED
      }
      if (!updates.status && data.error != null) {
        updates.status = ToolCallStatus.FAILED
      }
      // P21/P22 修复：非保护状态下进入终态也清除审批状态
      if (updates.status && isTerminalStatus(updates.status) && existing.approval) {
        updates.approval = null
      }
    }
    // 进入终态时设置 completed_at
    if (updates.status && isTerminalStatus(updates.status) && !existing.completed_at) {
      updates.completed_at = new Date().toISOString()
    }
    Object.assign(existing, updates)
    return existing.tool_call_id || existing.id
  }
  // 新建（容错场景：tool_result 先于 tool 到达）
  const toolData = _normalizeNewToolCall(data)
  // status 推导：覆盖 _normalizeNewToolCall 的默认值（state 不在 stateMap 时默认 running 不准确）
  // 优先级：显式 status > stateMap > result→COMPLETED > error→FAILED
  const stateMap = { 'input-available': 'running', 'output-available': 'completed', 'output-error': 'failed' }
  if (data.status !== undefined) {
    toolData.status = data.status
  } else if (data.state && stateMap[data.state]) {
    toolData.status = stateMap[data.state]
  } else if (toolData.result != null && data.state !== 'output-error') {
    toolData.status = ToolCallStatus.COMPLETED
  } else if (toolData.error != null) {
    toolData.status = ToolCallStatus.FAILED
  }
  // 终态时设置 completed_at
  if (toolData.status && isTerminalStatus(toolData.status) && !toolData.completed_at) {
    toolData.completed_at = new Date().toISOString()
  }
  const toolCallId = toolData.tool_call_id || toolData.id
  if (!toolCallId) return null
  toolCallMap.set(toolCallId, toolData)
  return toolCallId
}

/**
 * 将审批数据附加到 Map 中对应 toolCall 的 approval 字段
 *
 * Map 版本的 setApprovalToToolCall（sessionStore）。
 * _synthetic 占位机制：审批先于真实 tool 事件到达时，创建占位条目使 ToolCallCard 立即渲染审批面板。
 *
 * toolCall 已存在时，仅附加 approval 数据，不修改 status（审批态与工具执行态解耦）。
 * toolCall 不存在时，创建 _synthetic 占位条目（默认 status=RUNNING）。
 *
 * @param {Map} toolCallMap - toolCall Map
 * @param {string} toolCallId - 工具调用 ID（interrupt_id 或 tool_call_id）
 * @param {Object} approvalData - 审批事件数据
 * @returns {boolean} isSynthetic - 是否创建了 _synthetic 占位条目（true 时调用方应加入 pendingApprovals 队列）
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
  // toolCall 还未到达，创建 _synthetic 占位条目
  // 顶层 tool_call_id = interrupt_id（作为占位 key，与 Map key 一致）；
  // approval.tool_call_id 保留原始 LLM tool_call_id（approvalData.tool_call_id）
  const syntheticToolCall = {
    id: toolCallId,
    tool_call_id: toolCallId,
    interrupt_id: toolCallId,
    name: approvalData?.tool_name || 'unknown',
    tool_name: approvalData?.tool_name || 'unknown',
    parameters: approvalData?.parameters || approvalData?.args || {},
    status: ToolCallStatus.RUNNING,
    approval: approvalData,
    _synthetic: true,
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
  // 同时移除 altId（如 approvalData.tool_call_id）对应的条目
  if (pending.approvalData?.tool_call_id && pending.approvalData.tool_call_id !== toolCallId) {
    pendingMap.delete(pending.approvalData.tool_call_id)
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
  tc.status = status
  return true
}

/** 工具调用终态集合别名（向后兼容 _mergeToolCalls 内引用） */
const _TERMINAL_TOOL_STATUSES = _TERMINAL_TOOL_STATUSES_SET

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
 * - 本地 _synthetic 占位条目若已被真实 toolCall 替换，则丢弃
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
  const usedBackendIds = new Set()
  // 第一遍：遍历本地，匹配后端
  for (const local of existingList) {
    const localId = local.id || local.tool_call_id
    const backend = backendList.find(b => {
      const bId = b.id || b.tool_call_id
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
      if (local.status && _TERMINAL_TOOL_STATUSES.has(local.status)
        && backend.status && !_TERMINAL_TOOL_STATUSES.has(backend.status)) {
        mergedTc.status = local.status
      }
      // 保留本地审批状态（后端可能滞后）
      if (local.approval && backend.approval) {
        mergedTc.approval = { ...local.approval, ...backend.approval }
      } else if (local.approval) {
        mergedTc.approval = local.approval
      }
      merged.push(mergedTc)
      usedBackendIds.add(backend.id || backend.tool_call_id)
    } else {
      // 本地独有：可能是实时同步数据（_synthetic 已被替换的真实 toolCall），保留
      // _synthetic 占位若后端无对应，丢弃（已被真实 toolCall 替代或不再需要）
      if (!local._synthetic) {
        merged.push(local)
      }
    }
  }
  // 第二遍：追加后端独有
  for (const backend of backendList) {
    const bId = backend.id || backend.tool_call_id
    if (bId && !usedBackendIds.has(bId)) {
      merged.push(backend)
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
  const _NON_CONTENT_FIELDS = ['tokenCount', 'responseTime', 'model', 'backendId']

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
