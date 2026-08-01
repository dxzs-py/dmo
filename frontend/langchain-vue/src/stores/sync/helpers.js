import { logger } from '@/utils/logger'

/**
 * sync 模块通用查找辅助函数
 *
 * 抽取自 handleSessionEvent.js 中 9 类重复逻辑（按 messageId 查找 17 处、查找 session 19 处、
 * 查找最后 assistant 消息 9 处、获取当前版本 7 处、兜底拉取会话详情 3 处）。
 *
 * 设计原则：
 * - 纯函数式，不依赖外部闭包
 * - 接收 sessionStore 的函数显式参数化（getSession / ensureSessionLoaded）
 * - 接收 session 对象的函数不依赖 sessionStore（findMessageById / getLastAssistantMessage / getCurrentVersion）
 */

/**
 * 在指定会话中按 messageId 查找消息
 *
 * 匹配规则：backendId?.toString() === messageId?.toString()
 *          || id?.toString() === messageId?.toString()
 *
 * @param {Object} session - 会话对象（含 messages 数组）
 * @param {string|number|null|undefined} messageId - 消息 ID（backendId 或 id）
 * @returns {Object|null} 找到的消息对象，未找到返回 null
 */
export function findMessageById(session, messageId) {
  if (!session?.messages || messageId == null) return null
  return session.messages.find(m =>
    m.backendId?.toString() === messageId?.toString() ||
    m.id?.toString() === messageId?.toString()
  ) || null
}

/**
 * 在 sessionStore.sessions 中按 sessionId 查找会话
 *
 * @param {Object} sessionStore - session store 实例
 * @param {string} sessionId - 会话 ID
 * @returns {Object|undefined} 找到的会话对象，未找到返回 undefined
 */
export function getSession(sessionStore, sessionId) {
  if (!sessionStore?.sessions || !sessionId) return undefined
  return sessionStore.sessions.find(s => s.id === sessionId)
}

/**
 * 获取会话最后一条 assistant 消息
 *
 * @param {Object} session - 会话对象（含 messages 数组）
 * @returns {Object|null} 最后一条 assistant 消息，未找到返回 null
 */
export function getLastAssistantMessage(session) {
  if (!session?.messages) return null
  return [...session.messages].reverse().find(m => m.role === 'assistant') || null
}

/**
 * 获取消息当前版本数据
 *
 * @param {Object} message - 消息对象（含 versions 数组与 currentVersion 索引）
 * @returns {Object|undefined} 当前版本对象，不存在时返回 undefined
 */
export function getCurrentVersion(message) {
  if (!message?.versions) return undefined
  return message.versions[message.currentVersion]
}

/**
 * 兜底拉取会话详情
 *
 * 用于 sync 模块多处场景：session 不存在或消息为空时通过 loadSessionDetail 拉取后端数据。
 * 失败时仅记录日志，不抛异常（让调用方继续处理后续逻辑）。
 *
 * @param {Object} sessionStore - session store 实例
 * @param {string} sessionId - 会话 ID
 * @param {string} [callerTag='ensureSessionLoaded'] - 调用方标识，用于日志追溯
 * @returns {Promise<Object|null>} loadSessionDetail 返回的详情对象，失败返回 null
 */
export async function ensureSessionLoaded(sessionStore, sessionId, callerTag = 'ensureSessionLoaded') {
  if (!sessionStore?.loadSessionDetail || !sessionId) return null
  try {
    return await sessionStore.loadSessionDetail(sessionId, { forceRefresh: true })
  } catch (e) {
    logger.warn(`[Sync] ${callerTag} 兜底加载会话失败: ${sessionId}`, e)
    return null
  }
}
