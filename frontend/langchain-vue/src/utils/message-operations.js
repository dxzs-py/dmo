function _findMatchingToolCall(toolCalls, data) {
  if (!toolCalls || toolCalls.length === 0) return -1
  if (data.id) {
    // 优先按 id 精确匹配
    const idx = toolCalls.findIndex(t => t.id === data.id)
    if (idx >= 0) return idx
    // id 不匹配时，仅匹配同 name 但尚未拥有 id 的条目
    // （处理 tool_call_chunks 先到无 id、后到有 id 的场景）
    // 不会匹配已有不同 id 的条目，避免同名不同 id 的 tool_call 互相覆盖
    if (data.name) {
      const nameIdx = toolCalls.findIndex(t => t.name === data.name && !t.id && !t.result)
      if (nameIdx >= 0) return nameIdx
    }
    return -1
  }
  if (data.name) {
    const idx = toolCalls.findIndex(t => t.name === data.name && !t.result)
    if (idx >= 0) return idx
  }
  return -1
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
    if (!merged.status && merged.state) {
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
    const updates = {}
    if (data.state !== undefined) updates.state = data.state
    if (data.status !== undefined) updates.status = data.status
    if (data.result !== undefined) updates.result = data.result
    if (data.error !== undefined) updates.error = data.error
    if (!updates.status && updates.state) {
      const stateMap = { 'input-available': 'running', 'output-available': 'completed', 'output-error': 'failed' }
      updates.status = stateMap[updates.state] || 'running'
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
 *
 * 当 updates 模式的 approval 事件先于 messages 模式的 tool 事件到达时，
 * setApprovalToToolCall 找不到 toolCall，会将审批数据缓存到 _pendingApprovals。
 * 本函数在 toolCall 被添加后调用，将缓存的审批数据匹配到对应的 toolCall。
 */
function _matchPendingApprovals(message, data) {
  if (!message._pendingApprovals || message._pendingApprovals.length === 0) return
  if (!message.toolCalls || message.toolCalls.length === 0) return

  const _getCmd = (t) => t.parameters?.command || t.args?.command
  const _getToolName = (t) => t.name || t.tool_name || t.function?.name

  const remaining = []
  for (const { toolCallId, approvalData } of message._pendingApprovals) {
    let matched = null

    // 0. llm_tool_call_id 精确匹配（后端注入的 LLM tool_call.id，最可靠）
    if (approvalData?.llm_tool_call_id) {
      matched = message.toolCalls.find(t => t.id === approvalData.llm_tool_call_id)
    }
    // 1. 按 id 精确匹配（interrupt.id）
    if (!matched) {
      matched = message.toolCalls.find(t => t.id === toolCallId)
    }
    // 2. 按 toolName + operation 内容匹配
    if (!matched) {
      const _op = approvalData?.operation || approvalData?.command
      if (approvalData?.tool_name && _op) {
        matched = message.toolCalls.find(t =>
          _getToolName(t) === approvalData.tool_name &&
          (_getCmd(t) === _op || Object.values(t.parameters || {}).some(v => String(v) === _op)) &&
          !t.approval
        )
      }
    }
    // 3. 按 toolName 匹配（最宽松，取最新未审批的）
    if (!matched && approvalData?.tool_name) {
      for (let i = message.toolCalls.length - 1; i >= 0; i--) {
        const t = message.toolCalls[i]
        if (_getToolName(t) === approvalData.tool_name && !t.approval) {
          matched = t
          break
        }
      }
    }

    if (matched) {
      matched.approval = approvalData
      if (approvalData?.state) {
        matched.status = approvalData.state === 'pending' ? 'pending_approval' : matched.status
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

  // 检查是否有缓存的待匹配审批数据（时序问题：approval 事件可能先于 tool 事件到达）
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
  }
}
