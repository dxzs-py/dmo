import { logger } from '@/utils/logger'
import { getEventSessionId } from '@/utils/eventRouting'

/**
 * @typedef {import('@/composables/useRealtimeSync').RealtimeEvent} RealtimeEvent
 */

/**
 * 创建 user 通道事件处理器
 *
 * 原逻辑位于 sync.js L176-422（handleUserEvent 与 applyUserEvent）。
 *
 * @param {Object} ctx - 依赖上下文
 * @param {Object} ctx.sessionStore - session store 实例
 * @param {Object} ctx.realtime - useRealtimeSync 返回的实例
 * @param {(event: RealtimeEvent) => Promise<void>} ctx.handleRealtimeEvent - 统一实时事件处理器
 *   （由 sync.js 主文件装配后传入，避免循环依赖）
 * @returns {{ handleUserEvent: (event: RealtimeEvent) => Promise<void>, applyUserEvent: (event: RealtimeEvent) => Promise<void> }}
 */
export const createHandleUserEvent = (ctx) => {
  const { sessionStore, realtime, handleRealtimeEvent } = ctx

  /**
   * 处理 user 通道事件
   * @param {RealtimeEvent} event
   */
  const handleUserEvent = async (event) => {
    await applyUserEvent(event)
  }

  /**
   * 应用 user 通道事件
   * @param {RealtimeEvent} event
   */
  const applyUserEvent = async (event) => {
    const payload = event.payload || event
    // user 通道事件（session_created/deleted/updated）的会话 ID 在 payload 内部
    // （后端 _publish_to_user_async 不注入顶层 session_id），统一经 getEventSessionId
    // 解析（payload.sessionId 兜底），再回退 payload.id（会话对象内嵌 id，user 通道特有）。
    const sessionId = getEventSessionId(event) || payload.id
    // 区分 replay 事件与实时事件（由 useRealtimeSync.dispatchEvent 注入）
    // replay 事件仅用于状态重建，不应触发副作用（自动订阅/自动切换），
    // 否则 N 条历史 session_created 会触发 N 次 subscribeSession + N 次快照请求
    // Task 7.1：_isReplay → isReplay（内部标记，不参与网络传输）
    const isReplay = event.isReplay === true

    switch (event.type) {
      case 'session_created': {
        if (!sessionId) {
          logger.warn('[Sync] session_created 事件缺少会话 ID')
          return
        }
        const sessionData = payload.session || payload
        const hasFullMeta = sessionData.title !== undefined && sessionData.createdAt !== undefined
        if (!hasFullMeta) {
          await sessionStore.loadSessionDetail(sessionId)
        } else {
          sessionStore.upsertSession(sessionData)
        }

        if (isReplay) {
          // replay 场景：仅重建会话列表，不自动订阅/切换
          // 原因：replay 出的 session_created 是历史事件，该会话已存在；
          // 用户切换到该会话时 ChatView 会主动 subscribe，无需在此预先订阅。
          // 若自动 subscribe，N 个历史会话 = N 次 WS subscribe + N 次后端权限查询
          // + N 次历史回放 + N 次快照请求 = 请求风暴根因。
          // 仅在 currentSessionId 为空时（首次加载无默认会话）选第一个作为默认。
          if (!sessionStore.currentSessionId) {
            sessionStore.currentSessionId = sessionId
            logger.info(`[Sync] replay 首次设置默认会话: ${sessionId}`)
          }
          logger.debug(`[Sync] replay session_created 仅重建列表: ${sessionId}`)
          break
        }

        // 触发浏览器去重：若本浏览器刚通过 HTTP 创建了此会话，跳过 subscribeSession + currentSessionId 切换
        // 原因：HTTP 响应已返回新会话信息，本地已完成订阅；WS 事件再次触发会重复订阅+请求
        if (sessionStore.consumeOptimisticSession(sessionId)) {
          // 仍 upsertSession 以防元数据更新（如服务端补全 title/created_at）
          sessionStore.upsertSession(sessionData)
          logger.info(`[Sync] session_created 乐观命中（本浏览器触发），跳过订阅+切换: ${sessionId}`)
          break
        }

        // 实时事件：非触发浏览器收到 session_created 后，自动切换到新 session，
        // 确保所有浏览器显示相同内容（实时同步核心需求）。
        if (sessionStore.currentSessionId !== sessionId) {
          logger.info(`[Sync] session_created 自动切换会话: ${sessionStore.currentSessionId} -> ${sessionId}`)
          sessionStore.currentSessionId = sessionId
        }
        // 自动订阅新会话的 WS session 频道
        // 非触发浏览器必须订阅，否则后续的 approval_* / tool_call_* 等事件会被
        // dispatchEvent 静默丢弃，导致非触发浏览器看不到审批面板。
        // replayFromSeq=0 覆盖 approval_* 系列事件在订阅生效前就已发布的竞态。
        // 统一使用 handleRealtimeEvent 作为唯一入口（与视图手动订阅一致），
        // 避免双重订阅导致 seq 状态污染和重复处理。
        realtime.subscribeSession(sessionId, handleRealtimeEvent, { replayFromSeq: 0 })
        logger.info(`[Sync] 会话创建并自动订阅: ${sessionId}`)
        break
      }
      case 'session_deleted':
        if (sessionId) {
          sessionStore.removeSession(sessionId)
          logger.info(`[Sync] 会话删除: ${sessionId}`)
        }
        break
      case 'session_updated': {
        if (sessionId) {
          const fields = payload.fields || payload
          const safeFields = {
            ...(fields.title !== undefined && { title: fields.title }),
            ...(fields.mode !== undefined && { mode: fields.mode }),
            ...(fields.selectedKnowledgeBases !== undefined && {
              selectedKnowledgeBases: fields.selectedKnowledgeBases,
            }),
            ...(fields.updatedAt !== undefined && { updatedAt: fields.updatedAt }),
          }
          sessionStore.updateSessionFields(sessionId, safeFields)
          logger.info(`[Sync] 会话更新: ${sessionId}`)
        }
        break
      }
      default:
        logger.debug(`[Sync] 未处理的 user 事件: ${event.type}`)
    }
  }

  return {
    handleUserEvent,
    applyUserEvent,
  }
}
