import { MessageRole } from '@/types'

/**
 * 创建消息对象
 * @param {string} id - 消息 ID
 * @param {string} role - 消息角色
 * @param {string} content - 消息内容
 * @param {Object} [extra] - 额外字段
 * @returns {Object} 消息对象
 */
export function createMessage(id, role, content, extra = {}) {
  return {
    id: id || Date.now().toString(),
    role,
    content,
    timestamp: new Date().toISOString(),
    ...extra,
  }
}

/**
 * 创建用户消息
 * @param {string} content - 消息内容
 * @returns {Object} 用户消息对象
 */
export function createUserMessage(content) {
  return createMessage(Date.now().toString(), MessageRole.USER, content)
}

/**
 * 创建助手消息
 * @param {string} [content=''] - 消息内容
 * @param {Object} [extra={}] - 额外字段
 * @returns {Object} 助手消息对象
 */
export function createAssistantMessage(content = '', extra = {}) {
  return createMessage((Date.now() + 1).toString(), MessageRole.ASSISTANT, content, extra)
}
