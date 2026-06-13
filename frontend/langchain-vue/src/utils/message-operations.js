import { PROTECTED_STATUSES, ToolCallStatus, mapApprovalStateToStatus } from '../types'

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

  // 0. llm_tool_call_id 精确匹配（最可靠）
  if (approvalData?.llm_tool_call_id) {
    const tc = toolCalls.find(t => t.id === approvalData.llm_tool_call_id)
    if (tc) return tc
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
    // 保护审批相关状态不被SSE流中的status/state覆盖
    // 但允许 approved 状态向 running/completed/failed 正常流转（审批通过后的工具执行流程）
    if (PROTECTED_STATUSES.includes(existing.status) && existing.approval) {
      const incomingStatus = data.status
      const isApprovedTransition = existing.status === 'approved' &&
        ['running', 'completed', 'failed'].includes(incomingStatus)
      if (!isApprovedTransition) {
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
    // 保护审批相关状态：当已有审批状态时，不覆盖 status
    // 但允许 approved 状态向 running/completed/failed 正常流转
    if (PROTECTED_STATUSES.includes(existing.status) && existing.approval) {
      const incomingStatus = data.status
      const isApprovedTransition = existing.status === 'approved' &&
        ['running', 'completed', 'failed'].includes(incomingStatus)
      if (isApprovedTransition) {
        if (data.status !== undefined) updates.status = data.status
      }
      // 否则保留审批状态，不更新 status
    } else {
      if (data.status !== undefined) updates.status = data.status
      if (!updates.status && updates.state) {
        const stateMap = { 'input-available': 'running', 'output-available': 'completed', 'output-error': 'failed' }
        updates.status = stateMap[updates.state] || 'running'
      }
    }
    Object.assign(message.toolCalls[idx], updates)
  } else {
    const toolData = { ...data }
    if (!toolData.status && toolData.state) {
      const stateMap = { 'input-available': 'running', 'output-available': 'completed', 'output-error': 'failed' }
      toolData.status = stateMap[toolData.state] || 'running'
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
