function _findMatchingToolCall(toolCalls, data) {
  if (!toolCalls || toolCalls.length === 0) return -1
  if (data.id) {
    const idx = toolCalls.findIndex(t => t.id === data.id)
    if (idx >= 0) return idx
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

export function addOrUpdateToolCallInLastMessage(sessions, sessionId, data) {
  const result = getLastAssistantMessage(sessions, sessionId)
  if (!result) return
  _addOrUpdateToolCallInMessage(result.message, data)
  const ver = result.message.versions?.[result.message.currentVersion]
  if (ver) _addOrUpdateToolCallInMessage(ver, data)
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
  }
}
