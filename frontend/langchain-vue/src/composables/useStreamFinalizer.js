import { useSessionStore } from '@/stores/session'
import { useSyncStore } from '@/stores/sync'
import { chatFinalize } from '@/api/chat'
import { logger } from '@/utils/logger'
import { StreamState } from '@/types'

/**
 * 统一流式最终化 composable
 *
 * 当前仅审批恢复流（approval.js _executeApprovalStream 的 finally 块）使用；
 * chat.js sendMessage / regenerateMessage 尚未接入本 composable，
 * 其流式最终化仍由各自 onStreamEnd 内联实现。
 *
 * 最终化流程：
 * FINALIZING → waitForSyncLock → flushPendingSync → waitForSyncLock →
 * SYNCING → syncLastMessageToBackend → waitForSyncLock → chatFinalize → COMPLETED
 *
 * 使用方式：
 * ```js
 * const { finalizeStream } = useStreamFinalizer()
 * // 默认操作最后一条消息
 * await finalizeStream(sessionId, lastMsg, { allowCreate: false })
 * // 操作指定索引消息
 * await finalizeStream(sessionId, currentMsg, {
 *   messageIndex: idx,
 *   setStreamState: (state) => sessionStore.setStreamStateToMessageByIdx(sid, idx, state)
 * })
 * ```
 */
export function useStreamFinalizer() {
  const sessionStore = useSessionStore()
  const syncStore = useSyncStore()

  /**
   * 最终化流式输出
   *
   * @param {string} sessionId - 会话 ID
   * @param {object} lastMsg - 最后一条助手消息引用（用于读取 backendId/id 和状态守卫）
   * @param {{ allowCreate?: boolean, setStreamState?: (state: string) => void, messageIndex?: number }} [options]
   * @param {boolean} [options.allowCreate=false] - 是否允许创建新消息
   * @param {Function} [options.setStreamState] - 自定义状态设置函数，默认使用 setStreamStateToLastMessage
   * @param {number} [options.messageIndex] - 指定同步的消息索引，未传入时同步最后一条消息
   * @returns {Promise<boolean>} 是否成功完成最终化
   */
  async function finalizeStream(sessionId, lastMsg, { allowCreate = false, setStreamState, messageIndex } = {}) {
    if (!sessionId || !lastMsg) return false

    // 状态守卫：COMPLETED/ERROR 是终态，不应重复最终化
    // INTERRUPTED 在深度研究流完成后需要放行最终化（researchTaskId 存在时）
    if (lastMsg.streamState === StreamState.COMPLETED
        || lastMsg.streamState === StreamState.ERROR) {
      logger.debug(`[useStreamFinalizer] 消息状态为 ${lastMsg.streamState}，跳过最终化: session=${sessionId}`)
      return false
    }
    if (lastMsg.streamState === StreamState.INTERRUPTED && !lastMsg.researchTaskId) {
      logger.debug(`[useStreamFinalizer] 消息状态为 INTERRUPTED 且无 researchTaskId，跳过最终化: session=${sessionId}`)
      return false
    }

    const setState = setStreamState
      || ((state) => sessionStore.setStreamStateToLastMessage(sessionId, state))

    let completed = false
    try {
      // 1. FINALIZING：阻止 WebSocket 事件覆盖本地内容
      setState(StreamState.FINALIZING)
      sessionStore.clearToolSyncTimer(sessionId)

      // 2. 等待现有同步完成 → flush pending → 等待 flush 完成
      //    传入 messageIndex 时按索引 flush，否则 flush 最后一条消息
      await sessionStore.waitForSyncLock(sessionId)
      await sessionStore.flushPendingSync(sessionId, messageIndex !== undefined ? { messageIndex } : {})
      await sessionStore.waitForSyncLock(sessionId)

      // 3. SYNCING：明确表示正在执行 PATCH 同步
      setState(StreamState.SYNCING)

      // 4. 最终 PATCH → 等待 PATCH 完成
      //    传入 messageIndex 时按索引同步，否则同步最后一条消息
      if (messageIndex !== undefined) {
        await sessionStore.syncMessageToBackend(sessionId, messageIndex, { allowCreate })
      } else {
        await sessionStore.syncLastMessageToBackend(sessionId, { allowCreate })
      }
      await sessionStore.waitForSyncLock(sessionId)

      // 5. 通知后端广播 stream_finalized，让非请求浏览器可安全拉取后端数据
      //    失败时重试最多 3 次，避免单次网络抖动导致非请求浏览器卡在 FINALIZING
      const messageId = lastMsg.backendId || lastMsg.id
      for (let attempt = 0; attempt < 3; attempt++) {
        try {
          await chatFinalize(sessionId, messageId)
          logger.debug(`[useStreamFinalizer] 通知后端 stream_finalized: session=${sessionId}, msg=${messageId}`)
          break
        } catch (notifyErr) {
          if (attempt < 2) {
            logger.warn(`[useStreamFinalizer] chatFinalize 重试 ${attempt + 1}/3:`, notifyErr)
            await new Promise(r => setTimeout(r, 1000 * (attempt + 1)))
          } else {
            logger.warn('[useStreamFinalizer] chatFinalize 重试耗尽（不影响本地最终化）:', notifyErr)
          }
        }
      }

      // 6. COMPLETED：所有同步完成后才标记，解除合并保护
      setState(StreamState.COMPLETED)
      sessionStore.touchSessionUpdatedAt(sessionId)
      completed = true
    } catch (error) {
      logger.error('[useStreamFinalizer] finalizeStream failed:', error)
      // 确保 streamState 被设置为 COMPLETED，避免卡在 finalizing/syncing
      setState(StreamState.COMPLETED)
    } finally {
      // 7. 通知 syncStore 流式结束，恢复 WebSocket 事件处理
      syncStore.stopStreaming(sessionId)
    }
    return completed
  }

  return {
    finalizeStream,
  }
}
