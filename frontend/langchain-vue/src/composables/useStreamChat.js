import { ref, readonly } from 'vue'
import { chatAPI } from '@/api/chat'
import { useRealtimeSync } from './useRealtimeSync'
import { useSessionStore } from '@/stores/session'
import { StreamState } from '@/types'
import { logger } from '../utils/logger'

export const CONNECTION_STATUS = {
  CONNECTED: 'connected',
  DISCONNECTED: 'disconnected',
  RECONNECTING: 'reconnecting',
  CONNECTING: 'connecting',
}

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
 * @returns {Promise<'completed'|'interrupted'>}
 */
function waitForStreamEnd(sessionId, messageId, signal) {
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

    /** 定位等待消息：优先按 messageId，兜底最后一条 assistant 消息 */
    const findTargetMessage = () => {
      const session = sessionStore.sessions.find(s => s.id === sessionId)
      const messages = session?.messages || []
      if (messageId) {
        const byId = messages.find(m =>
          m.backendId?.toString() === messageId.toString()
          || m.id?.toString() === messageId.toString()
        )
        if (byId) return byId
      }
      for (let i = messages.length - 1; i >= 0; i--) {
        if (messages[i].role === 'assistant') return messages[i]
      }
      return null
    }

    /** 轮询兜底：消息状态到达终态即结束 */
    const poll = () => {
      const target = findTargetMessage()
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
  const connectionStatus = ref(CONNECTION_STATUS.DISCONNECTED)
  const lastError = ref(null)

  function abort() {
    if (abortController.value) {
      abortController.value.abort()
    }
    isStreaming.value = false
    abortController.value = null
    connectionStatus.value = CONNECTION_STATUS.DISCONNECTED
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
    connectionStatus.value = CONNECTION_STATUS.CONNECTING
    resetState()

    try {
      const response = await chatAPI.streamMessage(requestData, {
        signal: controller.signal,
      })

      connectionStatus.value = CONNECTION_STATUS.CONNECTED

      const resData = response.data?.data || {}
      if (resData.status !== 'started') {
        const error = new Error(resData.message || response.data?.message || '消息发送失败')
        logger.error('[StreamChat] 聊天执行启动失败:', error.message)
        lastError.value = error.message
        connectionStatus.value = CONNECTION_STATUS.DISCONNECTED
        throw error
      }

      const messageId = resData.messageId || null
      await waitForStreamEnd(requestData.sessionId, messageId, controller.signal)

      connectionStatus.value = CONNECTION_STATUS.DISCONNECTED
      return { success: true }
    } catch (error) {
      if (error.name === 'AbortError') {
        connectionStatus.value = CONNECTION_STATUS.DISCONNECTED
        return { success: false, aborted: true }
      }
      logger.error('[StreamChat] 异常:', error.message)
      lastError.value = error.message
      connectionStatus.value = CONNECTION_STATUS.DISCONNECTED
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
    connectionStatus: readonly(connectionStatus),
    lastError: readonly(lastError),
  }
}
