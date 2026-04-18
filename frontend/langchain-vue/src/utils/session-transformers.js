import { nanoid } from 'nanoid'
import settings from '../settings'

const API_SUCCESS_CODE = 200

export function isApiSuccess(response) {
  return response?.data?.code === API_SUCCESS_CODE
}

export function generateId() {
  return nanoid()
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
    messageCount: sessionObj.message_count || 0,
    messages: validMessages
      .map(transformBackendMessageToFrontend)
      .filter(msg => msg !== null),
    createdAt: sessionObj.created_at ? new Date(sessionObj.created_at).getTime() : Date.now(),
    updatedAt: sessionObj.updated_at ? new Date(sessionObj.updated_at).getTime() : Date.now(),
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

  const versions = msgObj.versions?.length > 0 ? msgObj.versions : [
    {
      id: msgObj.id?.toString() || generateId(),
      content: msgObj.content || '',
      sources: msgObj.sources || [],
      plan: msgObj.plan || null,
      chainOfThought: msgObj.chain_of_thought || null,
      toolCalls: msgObj.tool_calls || [],
      reasoning: msgObj.reasoning || null
    }
  ]

  const currentVersion = msgObj.current_version !== undefined ? msgObj.current_version : 0
  const activeVersion = versions[currentVersion] || versions[0]

  return {
    id: msgObj.id?.toString() || generateId(),
    role: role,
    content: activeVersion?.content || msgObj.content || '',
    sources: activeVersion?.sources || msgObj.sources || [],
    plan: activeVersion?.plan || msgObj.plan || null,
    chainOfThought: activeVersion?.chainOfThought || activeVersion?.chain_of_thought || msgObj.chain_of_thought || null,
    toolCalls: activeVersion?.toolCalls || activeVersion?.tool_calls || msgObj.tool_calls || [],
    reasoning: activeVersion?.reasoning || msgObj.reasoning || null,
    versions: versions,
    currentVersion: currentVersion,
    timestamp: msgObj.created_at ? new Date(msgObj.created_at).getTime() : Date.now(),
  }
}

export function transformFrontendMessageToBackend(msg) {
  const currentVersion = msg.versions?.[msg.currentVersion || 0] || msg
  return {
    role: msg.role,
    content: msg.content,
    sources: msg.sources || currentVersion.sources || [],
    plan: msg.plan || currentVersion.plan || null,
    chain_of_thought: msg.chainOfThought || currentVersion.chainOfThought || null,
    tool_calls: msg.toolCalls || currentVersion.toolCalls || [],
    reasoning: msg.reasoning || currentVersion.reasoning || null,
    current_version: msg.currentVersion || 0,
  }
}

export function getModeLabel(mode) {
  const modeLabels = {
    'basic-agent': '基础对话',
    'deep-thinking': '深度思考',
    'rag': 'RAG 问答',
    'workflow': '学习工作流',
    'deep-research': '深度研究',
    'guarded': '安全模式',
  }
  return modeLabels[mode] || mode
}

export function validateChatRequest(data) {
  const errors = []

  if (!data.message || typeof data.message !== 'string') {
    errors.push('消息内容不能为空')
  } else {
    const trimmedMessage = data.message.trim()
    if (trimmedMessage.length === 0) {
      errors.push('消息内容不能为空或仅包含空格')
    } else if (trimmedMessage.length > settings.API_VALIDATION.MESSAGE_MAX_LENGTH) {
      errors.push(`消息内容不能超过${settings.API_VALIDATION.MESSAGE_MAX_LENGTH}个字符`)
    }
  }

  if (data.mode && !settings.API_VALIDATION.ALLOWED_MODES.includes(data.mode)) {
    errors.push(`不支持的模式: ${data.mode}。允许的模式: ${settings.API_VALIDATION.ALLOWED_MODES.join(', ')}`)
  }

  if (data.session_id && !settings.API_VALIDATION.SESSION_ID_PATTERN.test(data.session_id)) {
    errors.push('会话ID格式无效')
  }

  if (data.chat_history && Array.isArray(data.chat_history)) {
    if (data.chat_history.length > settings.API_VALIDATION.CHAT_HISTORY_MAX_ITEMS) {
      errors.push(`聊天历史记录不能超过${settings.API_VALIDATION.CHAT_HISTORY_MAX_ITEMS}条`)
    }
    data.chat_history.forEach((msg, index) => {
      if (!['user', 'assistant', 'system'].includes(msg.role)) {
        errors.push(`聊天历史第${index + 1}条消息的角色无效`)
      }
      if (msg.content && msg.content.length > 50000) {
        errors.push(`聊天历史第${index + 1}条消息内容过长`)
      }
    })
  }

  return {
    valid: errors.length === 0,
    errors,
    sanitizedData: {
      ...data,
      message: data.message ? data.message.trim() : data.message,
    }
  }
}
