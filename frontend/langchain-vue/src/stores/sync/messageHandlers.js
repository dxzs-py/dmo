import { logger } from '@/utils/logger'
import { transformBackendMessageToFrontend } from '@/utils/session-transformers'
import { mergeMessageFromBackend, createMessageVersion } from '@/utils/message-operations'
import { StreamState } from '@/types'
import {
  findMessageById,
  getSession,
  getCurrentVersion,
} from './helpers'

/**
 * @typedef {import('@/composables/useRealtimeSync').RealtimeEvent} RealtimeEvent
 */

/**
 * 创建消息 CRUD 处理器
 *
 * 处理 message_added / message_updated / messages_deleted /
 * message_regenerated / message_regenerate_reverted 事件。
 *
 * @param {Object} ctx - 依赖上下文
 * @param {Object} ctx.sessionStore - session store 实例
 * @param {Set<string>} ctx.streamingSessions - reactive(new Set())，请求浏览器 SSE 活跃会话集合
 * @returns {{
 *   handleMessageAdded: (sessionId: string, messageData: Object) => void,
 *   handleMessageUpdated: (sessionId: string, messageId: string, fields: Object) => void,
 *   handleMessagesDeleted: (sessionId: string, ids: string[]) => void,
 *   handleMessageRegenerated: (sessionId: string, payload: Object) => void,
 *   handleMessageRegenerateReverted: (sessionId: string, payload: Object) => void,
 * }}
 */
export const createMessageHandlers = (ctx) => {
  const { sessionStore, streamingSessions } = ctx

  /**
   * 新增消息（幂等：已存在则合并）
   * @param {string} sessionId
   * @param {Object} messageData
   */
  const handleMessageAdded = (sessionId, messageData) => {
    if (!messageData || typeof messageData !== 'object') {
      logger.warn('[Sync] message_added 事件缺少消息数据')
      return
    }
    const session = getSession(sessionStore, sessionId)
    if (!session) {
      logger.warn(`[Sync] message_added 会话不存在: ${sessionId}`)
      return
    }
    const message = transformBackendMessageToFrontend(messageData)
    if (!message) {
      logger.warn('[Sync] message_added 消息转换失败')
      return
    }

    // 去重策略：
    // 1. 精确匹配：backendId 或 id 一致
    // 2. 占位消息合并：前端 sendMessage 创建的本地占位消息（backendId 尚未设置），
    //    从后向前查找最近一条无 backendId 的同角色消息作为占位进行合并。
    //    - assistant：content 为空即可（流式占位初始为空，SSE chunk 会追加到已合并的消息）
    //    - user：content 与后端消息一致
    let existing = session.messages?.find(m =>
      (m.backendId && m.backendId?.toString() === message.backendId?.toString()) ||
      (m.id && m.id?.toString() === message.id?.toString())
    )

    // 诊断日志：占位合并前的状态
    const placeholderCount = session.messages?.filter(m => !m.backendId).length || 0
    logger.info(
      `[Sync] handleMessageAdded 诊断: session=${sessionId}, ` +
      `incoming.role=${message.role}, incoming.backendId=${message.backendId}, ` +
      `messagesCount=${session.messages?.length || 0}, ` +
      `placeholderCount=${placeholderCount}, ` +
      `exactMatch=${!!existing}`
    )

    if (!existing && message.backendId) {
      for (let i = session.messages.length - 1; i >= 0; i--) {
        const m = session.messages[i]
        if (m.backendId) break
        if (m.role !== message.role) continue
        if (m.role === 'assistant') {
          // assistant 占位合并条件：content 为空 OR isStreaming===true（当前流式占位）
          // 场景：SSE chunk 可能比 WebSocket message_added 先到达，占位已有 content，
          //       若仅 content 为空时合并，会走"消息新增"分支，产生两个 AI 气泡
          // 安全性：一个 session 同时只有一个 streaming 占位；
          //         mergeMessageFromBackend 会保护流式期间 content 不被后端覆盖
          if (!m.content || m.content === '' || m.isStreaming === true) {
            existing = m
            break
          }
        } else if (m.role === 'user') {
          if (m.content === message.content) {
            existing = m
            break
          }
        }
      }
      if (existing) {
        logger.info(`[Sync] 匹配到前端占位消息: session=${sessionId}, frontendId=${existing.id}, backendId=${message.backendId}, role=${message.role}, isStreaming=${existing.isStreaming}, contentLen=${(existing.content || '').length}`)
      } else {
        // 非触发浏览器收到 message_added 时，本地无占位消息是正常行为
        // （非触发浏览器不创建占位消息，直接走"消息新增"分支）
        logger.debug(
          `[Sync] 占位合并失败(非触发浏览器正常): session=${sessionId}, role=${message.role}, ` +
          `incomingContent=${message.content?.substring(0, 50)}, ` +
          `placeholderCount=${placeholderCount}`
        )
      }
    }

    if (existing) {
      if (message.backendId && !existing.backendId) {
        existing.backendId = message.backendId
      }
      mergeMessageFromBackend(existing, message)
      logger.info(`[Sync] 消息已存在，合并更新: session=${sessionId}, message=${message.backendId || message.id}`)
    } else {
      session.messages.push(message)
      session.messageCount = session.messages.length
      logger.info(`[Sync] 消息新增: session=${sessionId}, message=${message.backendId || message.id}`)
    }
    session.updatedAt = Date.now()

    // 消息新增/合并后，同步 toolCallsMap 到该消息的 toolCalls 数组
    // 场景：非触发浏览器 tool_call_* 事件可能先于 message_added 到达，Map 中已有数据
    // 此时新合并的 assistant 消息尚未派生 toolCalls，导致审批组件以降级方式渲染在错误位置
    if (message.role === 'assistant') {
      sessionStore.syncMessageToolCalls(sessionId, message)
    }
  }

  /**
   * 更新消息（携带完整消息数据时直接合并）
   * @param {string} sessionId
   * @param {string} messageId
   * @param {Object} fields
   */
  const handleMessageUpdated = (sessionId, messageId, fields) => {
    if (!messageId) {
      logger.warn('[Sync] message_updated 事件缺少 message_id')
      return
    }
    const session = getSession(sessionStore, sessionId)
    if (!session?.messages) {
      logger.warn(`[Sync] message_updated 会话不存在或无消息: ${sessionId}`)
      return
    }
    const message = findMessageById(session, messageId)
    if (!message) {
      // 非触发浏览器可能因时序竞态（message_updated 先于 message_added 处理完）暂时找不到消息
      // 后续的 message_added 或全量同步会补偿，降级为 debug 避免干扰
      logger.debug(`[Sync] message_updated 未找到消息(时序竞态，后续补偿): session=${sessionId}, message=${messageId}`)
      return
    }

    // 幂等保护：跳过已处理过的旧事件（WebSocket 重连回放场景）
    const updateSeq = fields?._seq || fields?.seq
    if (updateSeq && message._lastUpdateSeq && updateSeq <= message._lastUpdateSeq) {
      logger.debug(`[Sync] message_updated 重复事件跳过: seq=${updateSeq}, lastSeq=${message._lastUpdateSeq}`)
      return
    }

    // SSE 流式期间（触发浏览器），仅处理 tool_calls 字段更新，
    // 跳过 content/reasoning/sources/suggestions/context（由 SSE 负责）。
    // 整体跳过 message_updated 会导致 tool_calls 也被跳过，触发浏览器工具卡片缺失，
    // 因此仅跳过 SSE 负责的字段。
    if (streamingSessions.has(sessionId)) {
      if (fields?.message) {
        // 嵌套结构：仅保留 tool_calls，剔除 SSE 负责的字段后走 mergeMessageFromBackend
        const filteredMessage = { ...fields.message }
        delete filteredMessage.content
        delete filteredMessage.reasoning
        delete filteredMessage.sources
        delete filteredMessage.suggestions
        delete filteredMessage.context
        // 仅当存在 toolCalls 或用于匹配的 id 时才合并
        if (filteredMessage.toolCalls !== undefined || filteredMessage.id !== undefined) {
          const backendMessage = transformBackendMessageToFrontend(filteredMessage)
          if (backendMessage) {
            mergeMessageFromBackend(message, backendMessage)
          }
        }
      } else {
        // 扁平结构：仅提取 toolCalls 字段
        if (fields?.toolCalls !== undefined) {
          // 空值保护：后端 toolCalls 为空数组时不覆盖本地审批记录
          if (Array.isArray(fields.toolCalls) && fields.toolCalls.length === 0
              && Array.isArray(message.toolCalls) && message.toolCalls.length > 0) {
            logger.debug(`[Sync] message_updated 流式期间保留本地 toolCalls（后端为空）: message=${messageId}`)
          } else {
            // 走 mergeMessageFromBackend 而非 Object.assign，
            // 确保 mergeToolCalls 的状态优先级保护生效，防止后端滞后快照（status=running）
            // 覆盖本地高优先级状态（status=completed）
            mergeMessageFromBackend(message, { toolCalls: fields.toolCalls })
          }
        }
      }

      // 记录已处理的 seq，用于后续去重
      if (updateSeq) {
        message._lastUpdateSeq = updateSeq
      }
      session.updatedAt = Date.now()
      logger.info(`[Sync] 消息更新(流式期间仅 toolCalls): session=${sessionId}, message=${messageId}`)
      return
    }

    // 非流式期间：保持原有完整合并逻辑
    if (fields?.message) {
      const backendMessage = transformBackendMessageToFrontend(fields.message)
      if (backendMessage) {
        mergeMessageFromBackend(message, backendMessage)
      }
    } else {
      // toolCalls 空值保护：后端 toolCalls 为空数组时不覆盖本地审批记录
      // 场景：深度研究 writeback 时后端 ChatMessage.toolCalls 可能为空，
      // 但本地已有审批记录（通过 approval_* 系列事件同步），直接覆盖会导致审批卡片消失
      if (Array.isArray(fields.toolCalls) && fields.toolCalls.length === 0
          && Array.isArray(message.toolCalls) && message.toolCalls.length > 0) {
        const filtered = { ...fields }
        delete filtered.toolCalls
        logger.debug(`[Sync] message_updated 保留本地 toolCalls（后端为空）: message=${messageId}`)
        mergeMessageFromBackend(message, filtered)
      } else {
        // 走 mergeMessageFromBackend 而非 Object.assign，
        // 确保 mergeToolCalls 的状态优先级保护生效，防止后端滞后快照覆盖本地高优先级状态。
        // fields 可能包含 toolCalls 之外的字段（如 reasoning/sources 等），
        // mergeMessageFromBackend 会按字段类型分别处理
        mergeMessageFromBackend(message, fields)
      }
    }

    // 记录已处理的 seq，用于后续去重
    if (updateSeq) {
      message._lastUpdateSeq = updateSeq
    }

    session.updatedAt = Date.now()
    logger.info(`[Sync] 消息更新: session=${sessionId}, message=${messageId}`)
  }

  /**
   * 批量删除消息
   * @param {string} sessionId
   * @param {string[]} ids
   */
  const handleMessagesDeleted = (sessionId, ids) => {
    if (!ids?.length) {
      logger.warn('[Sync] messages_deleted 事件缺少删除 ID 列表')
      return
    }
    sessionStore.removeMessagesByIds(sessionId, ids)
    logger.info(`[Sync] 消息删除: session=${sessionId}, count=${ids.length}`)
  }

  /**
   * 重新生成消息事件（message_regenerated）
   *
   * 后端在 archive_current_version 后广播此事件，前端镜像相同逻辑：
   * 1. 归档当前版本到 versions 数组（保留 content/toolCalls/sources/reasoning 等快照）
   * 2. 追加新空版本并设置 currentVersion 指向它
   * 3. 清空顶层 content/toolCalls/sources/reasoning/suggestions/context
   * 4. 标记 streamState=STREAMING，让后续 SSE chunks 追加到新空版本
   *
   * 幂等保护：
   * - 若 currentVersion 指向的版本已是空 STREAMING 版本（content 为空且无 toolCalls），
   *   说明事件已处理过，跳过避免重复归档（应对 WebSocket 重连回放场景）。
   *
   * 事件顺序保证：
   * - 后端先广播 message_regenerated（Redis pubsub），再返回 SSE 流式响应；
   * - Redis pubsub 快于 HTTP 流式首字节，确保事件先于 SSE 首 chunk 到达。
   *
   * @param {string} sessionId
   * @param {Object} payload - 至少包含 message_id
   */
  const handleMessageRegenerated = (sessionId, payload) => {
    const messageId = payload.messageId
    if (!messageId) {
      logger.warn('[Sync] message_regenerated 事件缺少 message_id')
      return
    }
    const session = getSession(sessionStore, sessionId)
    if (!session?.messages) {
      logger.warn(`[Sync] message_regenerated 会话不存在或无消息: ${sessionId}`)
      return
    }
    const message = findMessageById(session, messageId)
    if (!message) {
      logger.warn(`[Sync] message_regenerated 未找到消息: session=${sessionId}, message=${messageId}`)
      return
    }

    // 幂等保护：currentVersion 指向的版本已是空 STREAMING 版本时跳过
    if (Array.isArray(message.versions) && message.versions.length > 0) {
      const currentVer = getCurrentVersion(message)
      if (currentVer
          && currentVer.streamState === StreamState.STREAMING
          && !(currentVer.content || '').trim()
          && (!currentVer.toolCalls || currentVer.toolCalls.length === 0)) {
        logger.info(`[Sync] message_regenerated 幂等跳过：当前版本已是空 STREAMING 版本: session=${sessionId}, message=${messageId}`)
        return
      }
    }

    // 1. 归档当前版本：versions 不存在或为空时用 createMessageVersion 初始化
    if (!Array.isArray(message.versions) || message.versions.length === 0) {
      message.versions = [createMessageVersion(message)]
      message.currentVersion = 0
    } else {
      // 将当前顶层字段同步到当前版本快照，确保归档前快照是最新的
      // （前端 versions[currentVersion] 通常已与顶层同步，此处显式同步兜底）
      const currentVer = getCurrentVersion(message)
      if (currentVer) {
        currentVer.content = message.content || ''
        currentVer.sources = message.sources || []
        currentVer.toolCalls = message.toolCalls || []
        currentVer.reasoning = message.reasoning || null
        currentVer.suggestions = message.suggestions || null
        currentVer.context = message.context || null
        currentVer.streamState = message.streamState || StreamState.COMPLETED
        currentVer.isStreaming = false
      }
    }

    // 2. 追加新空版本（与后端 archive_current_version 的空版本结构对齐）
    const newVersion = {
      id: message.id,
      content: '',
      sources: [],
      toolCalls: [],
      reasoning: null,
      suggestions: null,
      context: null,
      attachmentIds: message.attachmentIds || [],
      attachments: message.attachments || [],
      streamState: StreamState.STREAMING,
      isStreaming: true,
    }
    message.versions.push(newVersion)

    // 3. 设置 currentVersion 指向新空版本
    message.currentVersion = message.versions.length - 1

    // 4. 清空顶层字段（流式输出会重新填充）
    message.content = ''
    message.toolCalls = []
    message.sources = []
    message.reasoning = null
    message.suggestions = null
    message.context = null
    message.images = []

    // 5. 标记流式状态供 UI 显示
    message.streamState = StreamState.STREAMING
    message.isStreaming = true

    session.updatedAt = Date.now()
    logger.info(`[Sync] 消息重新生成版本归档: session=${sessionId}, message=${messageId}, versions=${message.versions.length}`)
  }

  /**
   * 重新生成失败回滚事件（message_regenerate_reverted）
   *
   * 后端在重新生成启动即失败（无任何输出内容）时，删除空版本并恢复上一个版本数据。
   * 前端镜像此操作：弹出最后一个空版本，恢复 currentVersion 和顶层字段。
   *
   * @param {string} sessionId
   * @param {Object} payload - 包含 message_id 和回滚后的完整 message 数据
   */
  const handleMessageRegenerateReverted = (sessionId, payload) => {
    const messageId = payload.messageId
    const backendMessage = payload.message
    if (!messageId) {
      logger.warn('[Sync] message_regenerate_reverted 事件缺少 message_id')
      return
    }
    const session = getSession(sessionStore, sessionId)
    if (!session?.messages) {
      logger.warn(`[Sync] message_regenerate_reverted 会话不存在: ${sessionId}`)
      return
    }
    const message = findMessageById(session, messageId)
    if (!message) {
      logger.warn(`[Sync] message_regenerate_reverted 未找到消息: ${messageId}`)
      return
    }

    // 弹出最后一个（空的失败版本）
    if (Array.isArray(message.versions) && message.versions.length > 1) {
      message.versions.pop()
      message.currentVersion = message.versions.length - 1
    }

    // 用后端回滚后的完整数据恢复顶层字段
    if (backendMessage) {
      const transformed = transformBackendMessageToFrontend(backendMessage)
      if (transformed) {
        message.content = transformed.content
        message.toolCalls = transformed.toolCalls
        message.sources = transformed.sources
        message.reasoning = transformed.reasoning
        message.suggestions = transformed.suggestions
        message.context = transformed.context
        message.versions = transformed.versions
        message.currentVersion = transformed.currentVersion
      }
    } else {
      // 无后端数据时，用当前版本恢复
      const ver = getCurrentVersion(message)
      if (ver) {
        message.content = ver.content || ''
        message.toolCalls = ver.toolCalls || []
        message.sources = ver.sources || []
        message.reasoning = ver.reasoning || null
      }
    }

    message.streamState = StreamState.COMPLETED
    message.isStreaming = false

    session.updatedAt = Date.now()
    logger.info(`[Sync] 消息重新生成失败回滚: session=${sessionId}, message=${messageId}`)
  }

  return {
    handleMessageAdded,
    handleMessageUpdated,
    handleMessagesDeleted,
    handleMessageRegenerated,
    handleMessageRegenerateReverted,
  }
}
