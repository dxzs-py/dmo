import { ref, readonly } from 'vue'
import { chatAPI } from '@/api/chat'
import { useRealtimeSync } from './useRealtimeSync'
import { useSessionStore } from '@/stores/session'
import { resolveStreamTargetMessage } from '@/utils/messageOperations'
import { StreamState } from '@/types'
import { logger } from '../utils/logger'

/** 消息流结束状态轮询间隔（ms） */
const STREAM_END_POLL_INTERVAL_MS = 300

/**
 * 等待指定会话消息的流式结束信号
 *
 * 执行与连接解耦后，chat agent 由 FastAPI 执行服务单协程运行，Django 仅发布
 * SIGNAL_START 信令返回 JSON（{ status: 'started' }）。流式输出经 WebSocket 广播，
 * 本函数等待该消息的流结束信号后 resolve：
 * - stream_completed：流正常结束（含审批挂起后恢复完成）
 * - stream_interrupted：流被中断（深度研究模式，研究转移后台执行）
 *
 * 兜底路径（事件因订阅时序 / replay 缺失而错过时）：
 * - 消息 streamState 变为 COMPLETED / ERROR
 * - 消息为 INTERRUPTED 且携带 researchTaskId（深度研究模式，chat 流已结束）
 *
 * 审批挂起期间消息为 INTERRUPTED（无 researchTaskId），本函数**不**结束，
 * 继续等待审批恢复后的 stream_completed —— 与旧 SSE 流"审批期间连接挂起"语义一致。
 *
 * @param {string} sessionId - 会话 ID
 * @param {string|null} messageId - assistant 消息 ID（后端返回的 message_id）
 * @param {AbortSignal} [signal] - 中止信号（用户点击停止时中止等待）
 * @returns {Promise<'completed'|'interrupted'|'deleted'>}
 *   - completed / interrupted：流结束信号
 *   - deleted：等待期间会话被删除（D-7：target 永远找不到会让轮询 interval
 *     与订阅永不清理，检测到会话不存在立即退出并回收资源）
 */
export function waitForStreamEnd(sessionId, messageId, signal) {
  return new Promise((resolve, reject) => {
    if (!sessionId) {
      resolve('completed')
      return
    }
    const realtime = useRealtimeSync()
    const sessionStore = useSessionStore()

    let settled = false
    let unsubscribe = () => {}
    let pollTimer = null

    const cleanup = () => {
      if (settled) return
      settled = true
      unsubscribe()
      if (pollTimer) {
        clearInterval(pollTimer)
        pollTimer = null
      }
      if (signal) {
        signal.removeEventListener('abort', onAbort)
      }
    }
    const done = (result) => {
      cleanup()
      resolve(result)
    }
    const fail = (err) => {
      cleanup()
      reject(err)
    }
    const onAbort = () => {
      fail(new DOMException('Aborted', 'AbortError'))
    }

    /**
     * 轮询兜底：会话已删除立即退出（D-7）；消息状态到达终态即结束
     * （消息定位沿用 spec 组 5 收敛语义：resolveStreamTargetMessage 按
     * messageId 精确匹配、兜底末条 assistant）
     */
    const poll = () => {
      // D-7：流式期间会话被删除时，target 永远找不到会导致 poll interval
      // 与事件订阅永不清理（内存泄漏 + 幽灵等待）。检测到会话不在
      // session store 中立即结束等待（done 内部统一清理 interval 与订阅）。
      const session = sessionStore.sessions.find(s => s.id === sessionId)
      if (!session) {
        done('deleted')
        return
      }
      const target = resolveStreamTargetMessage(session, messageId)
      // 消息尚未同步进 store（流刚启动的窗口期）：保持等待，不视为终态
      if (!target) return
      if (target.streamState === StreamState.COMPLETED) {
        done('completed')
        return
      }
      if (target.streamState === StreamState.ERROR) {
        fail(new Error(target.errorMessage || '流式执行失败'))
        return
      }
      // 深度研究模式：chat 流已结束（研究后台进行），事件可能已错过，轮询兜底
      if (target.streamState === StreamState.INTERRUPTED && target.researchTaskId) {
        done('interrupted')
      }
    }

    /** 事件驱动：stream_completed / stream_interrupted 到达即结束 */
    const handleEvent = (event) => {
      const type = event?.type
      if (type !== 'stream_completed' && type !== 'stream_interrupted') return
      const payload = event.payload || event
      const eventMessageId = payload.messageId || payload.data?.messageId
      // 事件携带 messageId 且与当前等待消息不匹配时忽略（其他消息的完成事件）
      if (eventMessageId && messageId && eventMessageId.toString() !== messageId.toString()) {
        return
      }
      done(type)
    }

    if (signal) {
      signal.addEventListener('abort', onAbort)
    }
    unsubscribe = realtime.subscribeSession(sessionId, handleEvent)
    pollTimer = setInterval(poll, STREAM_END_POLL_INTERVAL_MS)
    // 立即检查一次（事件可能在订阅建立前已到达，消息已进入终态）
    poll()
  })
}

export function useStreamChat() {
  const isStreaming = ref(false)
  const abortController = ref(null)
  const lastError = ref(null)

  function abort() {
    if (abortController.value) {
      abortController.value.abort()
    }
    isStreaming.value = false
    abortController.value = null
  }

  function resetState() {
    lastError.value = null
  }

  /**
   * 启动聊天执行（执行与连接解耦后的普通 POST）。
   *
   * 后端 ChatStreamView 创建消息对 + 广播 MESSAGE_ADDED 后发布 Redis 信令并返回
   * JSON（{ status: 'started' }），agent 由 FastAPI 执行服务单协程运行，流式输出 /
   * 工具 / 审批事件经 WebSocket 统一广播（sync store 消费）。本函数仅在收到
   * stream_completed / stream_interrupted 信号后返回，保证调用方（chat store）在
   * 消息流真正结束后才执行最终化（finalize），不会过早 PATCH 覆盖后端内容。
   *
   * @param {Object} requestData - 聊天请求数据（validateChatRequest 校验）
   * @returns {Promise<{success: boolean, aborted?: boolean, error?: Error}>}
   */
  async function streamChat(requestData) {
    const controller = new AbortController()
    abortController.value = controller
    isStreaming.value = true
    resetState()

    try {
      const response = await chatAPI.streamMessage(requestData, {
        signal: controller.signal,
      })

      const resData = response.data?.data || {}
      if (resData.status !== 'started') {
        const error = new Error(resData.message || response.data?.message || '消息发送失败')
        logger.error('[StreamChat] 聊天执行启动失败:', error.message)
        lastError.value = error.message
        throw error
      }

      const messageId = resData.messageId || null
      await waitForStreamEnd(requestData.sessionId, messageId, controller.signal)

      return { success: true }
    } catch (error) {
      if (error.name === 'AbortError') {
        return { success: false, aborted: true }
      }
      logger.error('[StreamChat] 异常:', error.message)
      lastError.value = error.message
      return { success: false, error }
    } finally {
      isStreaming.value = false
      abortController.value = null
    }
  }

  return {
    isStreaming,
    abortController,
    abort,
    streamChat,
    lastError: readonly(lastError),
  }
}
