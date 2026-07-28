import { apiClient } from './axios'
import settings from '../config/settings'
import { useUserStore } from '@/stores/user'
import { fetchSSE, readSSEStream } from '../utils/sse'

function validateChatRequest(data) {
  const errors = []
  if (!data.message || typeof data.message !== 'string') {
    errors.push('消息内容不能为空')
  } else {
    const trimmedMessage = data.message.trim()
    if (trimmedMessage.length === 0) errors.push('消息内容不能为空或仅包含空格')
    else if (trimmedMessage.length > settings.API_VALIDATION.MESSAGE_MAX_LENGTH)
      errors.push(`消息内容不能超过${settings.API_VALIDATION.MESSAGE_MAX_LENGTH}个字符`)
  }
  if (data.mode && !settings.API_VALIDATION.ALLOWED_MODES.includes(data.mode))
    errors.push(`不支持的模式: ${data.mode}。允许的模式: ${settings.API_VALIDATION.ALLOWED_MODES.join(', ')}`)
  if (data.session_id && !settings.API_VALIDATION.SESSION_ID_PATTERN.test(data.session_id))
    errors.push('会话ID格式无效')
  if (data.chat_history && Array.isArray(data.chat_history)) {
    if (data.chat_history.length > settings.API_VALIDATION.CHAT_HISTORY_MAX_ITEMS)
      errors.push(`聊天历史记录不能超过${settings.API_VALIDATION.CHAT_HISTORY_MAX_ITEMS}条`)
    data.chat_history.forEach((msg, index) => {
      if (!['user', 'assistant', 'system'].includes(msg.role))
        errors.push(`聊天历史第${index + 1}条消息的角色无效`)
      if (msg.content && msg.content.length > 50000)
        errors.push(`聊天历史第${index + 1}条消息内容过长`)
    })
  }
  return {
    valid: errors.length === 0,
    errors,
    sanitizedData: { ...data, message: data.message ? data.message.trim() : data.message },
  }
}

async function createStreamRequest(data, options = {}) {
  const validation = validateChatRequest(data)
  if (!validation.valid) throw new Error(validation.errors.join('; '))
  return fetchSSE('/chat/stream/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(validation.sanitizedData),
    signal: options.signal,
  })
}

export const chatAPI = {
  sendMessage(data) {
    const validation = validateChatRequest(data)
    if (!validation.valid) return Promise.reject(new Error(validation.errors.join('; ')))
    return apiClient.post('/chat/', validation.sanitizedData)
  },

  streamMessage(data, options = {}) {
    return createStreamRequest(data, options)
  },

  getModes() { return apiClient.get('/chat/modes/') },

  getSessions(params = {}) {
    const validatedParams = { ...params }
    if (validatedParams.page_size && validatedParams.page_size > 100) validatedParams.page_size = 100
    return apiClient.get('/chat/sessions/', { params: validatedParams })
  },

  createSession(data) {
    const sessionData = { ...data }
    if (sessionData.title && sessionData.title.length > 200) sessionData.title = sessionData.title.slice(0, 200)
    if (sessionData.mode && !settings.API_VALIDATION.ALLOWED_MODES.includes(sessionData.mode)) sessionData.mode = 'agent'
    return apiClient.post('/chat/sessions/create/', sessionData)
  },

  getSession(sessionId) {
    if (!sessionId || !settings.API_VALIDATION.SESSION_ID_PATTERN.test(sessionId))
      return Promise.reject(new Error('会话ID格式无效'))
    return apiClient.get(`/chat/sessions/${sessionId}/`)
  },

  updateSession(sessionId, data) {
    if (!sessionId) return Promise.reject(new Error('会话ID不能为空'))
    const updateData = {}
    if (data.title !== undefined) {
      if (typeof data.title !== 'string' || data.title.trim().length === 0)
        return Promise.reject(new Error('标题不能为空'))
      updateData.title = data.title.length > 200 ? data.title.slice(0, 200) : data.title.trim()
    }
    return apiClient.patch(`/chat/sessions/${sessionId}/`, updateData)
  },

  deleteSession(sessionId, params = {}) {
    if (!sessionId) return Promise.reject(new Error('会话ID不能为空'))
    return apiClient.delete(`/chat/sessions/${sessionId}/`, { params })
  },

  addMessage(sessionId, data) {
    if (!sessionId) return Promise.reject(new Error('会话ID不能为空'))
    const messageData = { ...data }
    if (messageData.content && messageData.content.length > 50000) messageData.content = messageData.content.slice(0, 50000)
    if (!['user', 'assistant', 'system'].includes(messageData.role)) messageData.role = 'user'
    return apiClient.post(`/chat/sessions/${sessionId}/messages/`, messageData)
  },

  addMessagesBatch(sessionId, data) {
    if (!sessionId) return Promise.reject(new Error('会话ID不能为空'))
    if (!Array.isArray(data.messages) || data.messages.length === 0)
      return Promise.reject(new Error('messages必须是非空数组'))
    if (data.messages.length > settings.API_VALIDATION.BATCH_CREATE_MAX_ITEMS)
      return Promise.reject(new Error(`批量创建数量不能超过${settings.API_VALIDATION.BATCH_CREATE_MAX_ITEMS}条`))
    return apiClient.post(`/chat/sessions/${sessionId}/messages/batch/`, data)
  },

  updateMessage(messageId, data) {
    if (!messageId) return Promise.reject(new Error('消息ID不能为空'))
    const updateData = { ...data }
    if (updateData.content && updateData.content.length > 50000) updateData.content = updateData.content.slice(0, 50000)
    return apiClient.patch(`/chat/messages/${messageId}/`, updateData)
  },

  deleteMessage(messageId) {
    if (!messageId) return Promise.reject(new Error('消息ID不能为空'))
    return apiClient.delete(`/chat/messages/${messageId}/delete/`)
  },

  deleteMessagePair(sessionId, userMessageId) {
    if (!sessionId) return Promise.reject(new Error('会话ID不能为空'))
    if (!userMessageId) return Promise.reject(new Error('用户消息ID不能为空'))
    return apiClient.delete(`/chat/sessions/${sessionId}/messages/pair/delete/`, { data: { user_message_id: userMessageId } })
  },

  uploadAttachment(sessionId, file, { onUploadProgress, signal } = {}) {
    if (!sessionId) return Promise.reject(new Error('会话ID不能为空'))
    if (!file) return Promise.reject(new Error('请选择要上传的文件'))
    const formData = new FormData()
    formData.append('file', file)
    return apiClient.post(`/attachments/sessions/${sessionId}/upload/`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      onUploadProgress: onUploadProgress || undefined,
      signal: signal || undefined,
    })
  },

  getAttachments(sessionId) {
    if (!sessionId) return Promise.reject(new Error('会话ID不能为空'))
    return apiClient.get(`/attachments/sessions/${sessionId}/list/`)
  },

  deleteAttachment(attachmentId) {
    if (!attachmentId) return Promise.reject(new Error('附件ID不能为空'))
    return apiClient.delete(`/attachments/${attachmentId}/`)
  },

  compactSession(sessionId) {
    if (!sessionId) return Promise.reject(new Error('会话ID不能为空'))
    return apiClient.post(`/chat/sessions/${sessionId}/compact/`)
  },

  clearSessionMessages(sessionId) {
    if (!sessionId) return Promise.reject(new Error('会话ID不能为空'))
    return apiClient.delete(`/chat/sessions/${sessionId}/messages/`)
  },

  getCommands() { return apiClient.get('/chat/commands/') },
  executeCommand(command, sessionId = null) { return apiClient.post('/chat/commands/execute/', { command, session_id: sessionId }) },
  getProjectContext(path = null) { return apiClient.get('/chat/project-context/', { params: path ? { path } : {} }) },
  finalizeStream: (sessionId, messageId) => chatFinalize(sessionId, messageId),
}

/**
 * 通知后端流式输出已最终化
 *
 * 前端 useStreamFinalizer 在本地完成 FINALIZING → SYNCING → COMPLETED 后调用，
 * 后端发布 STREAM_FINALIZED 事件到 session 频道，通知非请求浏览器可安全拉取后端数据。
 *
 * @param {string} sessionId - 会话 ID
 * @param {string|number} messageId - 消息 ID
 * @returns {Promise} apiClient POST 响应
 */
export function chatFinalize(sessionId, messageId) {
  if (!sessionId) return Promise.reject(new Error('会话ID不能为空'))
  if (!messageId) return Promise.reject(new Error('消息ID不能为空'))
  return apiClient.post('/chat/finalize/', { session_id: sessionId, message_id: messageId })
}

export async function* streamChat(request) {
  const response = await createStreamRequest(request)
  const eventQueue = []
  let resolveEvent = null
  let done = false

  await readSSEStream(response, (parsed) => {
    if (resolveEvent) {
      resolveEvent(parsed)
      resolveEvent = null
    } else {
      eventQueue.push(parsed)
    }
  })

  while (eventQueue.length > 0) {
    yield eventQueue.shift()
  }
}
