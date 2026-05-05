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
