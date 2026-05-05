import settings from '../config/settings'
import { generateId } from './id'
import { getModeLabel } from './format'

const API_SUCCESS_CODE = 200

export { getModeLabel }

export function isApiSuccess(response) {
  return response?.data?.code === API_SUCCESS_CODE
}

export function transformBackendSessionToFrontend(session) {
  if (!session) {
    return null
  }

  const sessionObj = session || {}
  const rawMessages = sessionObj.messages || []
  const validMessages = Array.isArray(rawMessages) ? rawMessages : []

  const sanitized = {
    id: sessionObj.session_id || sessionObj.id,
    sessionId: sessionObj.session_id || sessionObj.id,
    title: sessionObj.title || '新对话',
    mode: sessionObj.mode || 'basic-agent',
    selectedKnowledgeBase: sessionObj.selected_knowledge_base || null,
    messageCount: sessionObj.message_count || 0,
    messages: validMessages
      .map(transformBackendMessageToFrontend)
      .filter(msg => msg !== null),
    createdAt: sessionObj.created_at ? new Date(sessionObj.created_at).getTime() : Date.now(),
    updatedAt: sessionObj.updated_at ? new Date(sessionObj.updated_at).getTime() : Date.now(),
  }

  if (!Array.isArray(sanitized.messages)) {
    sanitized.messages = []
  }

  if (!sanitized.id && sessionObj.id) {
    sanitized.id = sessionObj.id
    sanitized.sessionId = sessionObj.id
  }

  return sanitized
}

export function transformBackendMessageToFrontend(msg) {
  if (!msg) {
    return null
  }

  const msgObj = msg || {}

  let role = msgObj.role
  if (!role || !['user', 'assistant', 'system'].includes(role)) {
    role = 'user'
  }

  const backendVersions = msgObj.versions
  const hasValidVersions = Array.isArray(backendVersions) && backendVersions.length > 0

  let versions
  if (hasValidVersions) {
    versions = backendVersions.map(v => ({
      id: v.id?.toString() || generateId(),
      content: v.content || '',
      sources: v.sources || [],
      plan: v.plan || null,
      chainOfThought: v.chain_of_thought || v.chainOfThought || null,
      toolCalls: v.tool_calls || v.toolCalls || [],
      reasoning: v.reasoning || null,
      suggestions: v.suggestions || null,
      context: v.context || null,
    }))
  } else {
    const version = {
      id: msgObj.id?.toString() || generateId(),
      content: msgObj.content || '',
      sources: msgObj.sources || [],
      plan: msgObj.plan || null,
      chainOfThought: msgObj.chain_of_thought || null,
      toolCalls: msgObj.tool_calls || [],
      reasoning: msgObj.reasoning || null,
      suggestions: msgObj.suggestions || null,
      context: msgObj.context || null,
    }
    versions = [version]
  }

  const currentVersion = typeof msgObj.current_version === 'number' && msgObj.current_version >= 0
    ? Math.min(msgObj.current_version, versions.length - 1)
    : 0

  const activeVersion = versions[currentVersion]

  return {
    id: msgObj.id?.toString() || generateId(),
    backendId: msgObj.id,
    role: role,
    content: activeVersion.content,
    sources: activeVersion.sources,
    plan: activeVersion.plan,
    chainOfThought: activeVersion.chainOfThought,
    toolCalls: activeVersion.toolCalls,
    reasoning: activeVersion.reasoning,
    suggestions: activeVersion.suggestions,
    context: activeVersion.context,
    attachmentIds: msgObj.attachment_ids || msgObj.attachmentIds || [],
    versions: versions,
    currentVersion: currentVersion,
    timestamp: msgObj.created_at ? new Date(msgObj.created_at).getTime() : Date.now(),
    model: msgObj.model || null,
    tokenCount: msgObj.token_count || 0,
    cost: msgObj.cost || 0,
    responseTime: msgObj.response_time || 0,
  }
}

export function transformFrontendMessageToBackend(msg) {
  const backendVersions = Array.isArray(msg.versions) && msg.versions.length > 0
    ? msg.versions.map(v => ({
        id: v.id,
        content: v.content || '',
        sources: v.sources || [],
        plan: v.plan || null,
        chain_of_thought: v.chainOfThought || null,
        tool_calls: v.toolCalls || [],
        reasoning: v.reasoning || null,
        suggestions: v.suggestions || null,
        context: v.context || null,
      }))
    : [{
        id: msg.id,
        content: msg.content || '',
        sources: msg.sources || [],
        plan: msg.plan || null,
        chain_of_thought: msg.chainOfThought || null,
        tool_calls: msg.toolCalls || [],
        reasoning: msg.reasoning || null,
        suggestions: msg.suggestions || null,
        context: msg.context || null,
      }]

  return {
    role: msg.role,
    content: msg.content,
    sources: msg.sources || [],
    plan: msg.plan || null,
    chain_of_thought: msg.chainOfThought || null,
    tool_calls: msg.toolCalls || [],
    reasoning: msg.reasoning || null,
    suggestions: msg.suggestions || null,
    context: msg.context || null,
    attachment_ids: msg.attachmentIds || [],
    versions: backendVersions,
    current_version: typeof msg.currentVersion === 'number' ? msg.currentVersion : 0,
    model: msg.model || null,
    token_count: msg.tokenCount || 0,
    cost: msg.cost || 0,
    response_time: msg.responseTime || 0,
  }
}
