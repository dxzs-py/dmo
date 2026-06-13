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

  // 合并连续的 assistant 消息：
  // 竞态条件可能导致同一个 AI 回复被保存为两条消息：
  //   1) 含 tool_calls 但 content 为空的 assistant 消息
  //   2) 含 content 但 tool_calls 为空的 assistant 消息
  // 需要将它们合并为一条完整的 assistant 消息
  const mergedMessages = _mergeConsecutiveAssistantMessages(validMessages)

  const sanitized = {
    id: sessionObj.session_id || sessionObj.id,
    sessionId: sessionObj.session_id || sessionObj.id,
    title: sessionObj.title || '新对话',
    mode: sessionObj.mode || 'agent',
    selectedKnowledgeBase: sessionObj.selected_knowledge_base || null,
    selectedKnowledgeBases: sessionObj.selected_knowledge_bases || [],
    messageCount: sessionObj.message_count || 0,
    messages: mergedMessages
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

/**
 * 合并连续的 assistant 消息。
 * 当 syncLastMessageToBackend 竞态导致同一 AI 回复被保存为多条消息时，
 * 将含 tool_calls 的消息与后续含 content 的消息合并。
 */
function _mergeConsecutiveAssistantMessages(messages) {
  if (!messages || messages.length <= 1) return messages

  const result = []
  let i = 0
  while (i < messages.length) {
    const msg = messages[i]

    if (msg.role === 'assistant' && i + 1 < messages.length) {
      const nextMsg = messages[i + 1]

      // 情况1：当前消息有 tool_calls 但 content 为空，下一条也是 assistant
      if (_hasToolCalls(msg) && !_hasContent(msg) && nextMsg.role === 'assistant') {
        const merged = { ...msg }
        // 合并 content
        merged.content = nextMsg.content || msg.content || ''
        // 合并 tool_calls（保留当前消息的 tool_calls）
        if (!_hasToolCalls(merged) && _hasToolCalls(nextMsg)) {
          merged.tool_calls = nextMsg.tool_calls
        }
        // 合并其他字段（plan, chain_of_thought, reasoning 等）
        for (const field of ['plan', 'chain_of_thought', 'reasoning', 'suggestions', 'sources']) {
          if (!merged[field] && nextMsg[field]) {
            merged[field] = nextMsg[field]
          }
        }
        // 合并 versions
        if (nextMsg.versions && nextMsg.versions.length > 0) {
          if (!merged.versions || merged.versions.length === 0) {
            merged.versions = nextMsg.versions
          } else {
            // 将下一条消息的 content 合并到当前消息的 versions 中
            for (const ver of merged.versions) {
              if (!ver.content && nextMsg.content) {
                ver.content = nextMsg.content
              }
            }
          }
        }
        result.push(merged)
        i += 2
        continue
      }
    }

    result.push(msg)
    i++
  }

  return result
}

function _hasToolCalls(msg) {
  return msg.tool_calls && Array.isArray(msg.tool_calls) && msg.tool_calls.length > 0
}

function _hasContent(msg) {
  return msg.content && msg.content.trim().length > 0
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
    toolCalls: activeVersion.toolCalls || [],
    approval: msgObj.approval || null,
    approvalState: msgObj.approval?.state
      || (activeVersion.toolCalls || []).find(tc => tc.approval)?.approval?.state
      || null,
    reasoning: activeVersion.reasoning,
    suggestions: activeVersion.suggestions,
    context: activeVersion.context,
    attachmentIds: msgObj.attachment_ids || msgObj.attachmentIds || [],
    attachments: msgObj.attachments || [],
    researchContext: msgObj.research_context || msgObj.researchContext || null,
    versions: versions,
    currentVersion: currentVersion,
    timestamp: msgObj.created_at ? new Date(msgObj.created_at).getTime() : Date.now(),
    model: msgObj.model || null,
    tokenCount: msgObj.token_count || 0,
    tokenDetail: msgObj.token_detail || null,
    responseTime: msgObj.response_time || 0,
    researchTaskId: msgObj.research_task_id || null,
    researchTaskDeleted: msgObj.research_task_deleted || null,
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
    approval: msg.approval || null,
    reasoning: msg.reasoning || null,
    suggestions: msg.suggestions || null,
    context: msg.context || null,
    attachment_ids: msg.attachmentIds || [],
    attachments: msg.attachments || [],
    research_context: msg.researchContext || null,
    versions: backendVersions,
    current_version: typeof msg.currentVersion === 'number' ? msg.currentVersion : 0,
    model: msg.model || null,
    token_count: msg.tokenCount || 0,
    token_detail: msg.tokenDetail || {},
    response_time: msg.responseTime || 0,
    research_task_id: msg.researchTaskId || null,
  }
}
