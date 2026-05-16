import { ref, readonly, computed } from 'vue'
import { chatAPI } from '../api'
import { readSSEStreamWithEvents } from '../utils/sse'
import { extractSSEError } from '../utils/api-error-handler'
import { logger } from '../utils/logger'

export const CONNECTION_STATUS = {
  CONNECTED: 'connected',
  DISCONNECTED: 'disconnected',
  RECONNECTING: 'reconnecting',
  CONNECTING: 'connecting',
}

export function useStreamChat() {
  const isStreaming = ref(false)
  const abortController = ref(null)
  const connectionStatus = ref(CONNECTION_STATUS.DISCONNECTED)
  const lastError = ref(null)
  const retryCount = ref(0)
  const streamStartTime = ref(null)
  const bytesReceived = ref(0)

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
    retryCount.value = 0
    bytesReceived.value = 0
    streamStartTime.value = null
  }

  async function streamChat(requestData, sessionOps) {
    const controller = new AbortController()
    abortController.value = controller
    isStreaming.value = true
    connectionStatus.value = CONNECTION_STATUS.CONNECTING
    resetState()
    streamStartTime.value = Date.now()

    const appendFn = sessionOps.appendToLastMessage
    const isStreamingRef = isStreaming

    const enhancedSessionOps = {
      ...sessionOps,
      setError: (errorMsg) => {
        lastError.value = errorMsg
        sessionOps.setError?.(errorMsg)
      },
      setMetadata: (metadata) => {
        sessionOps.setMetadata?.(metadata)
      },
    }

    try {
      const response = await chatAPI.streamMessage(requestData, {
        signal: controller.signal,
        onRetry: (attempt, maxRetries, delay) => {
          retryCount.value = attempt
          connectionStatus.value = CONNECTION_STATUS.RECONNECTING
          logger.info(`[StreamChat] 重试中 (${attempt}/${maxRetries}), ${delay}ms 后`)
        },
      })

      connectionStatus.value = CONNECTION_STATUS.CONNECTED

      if (!response.ok) {
        const error = await extractSSEError(response)
        logger.error('[StreamChat] 流式请求失败:', response.status, error.message)
        lastError.value = error.message
        connectionStatus.value = CONNECTION_STATUS.DISCONNECTED
        throw error
      }

      const originalAppend = appendFn
      const trackingAppend = (content) => {
        bytesReceived.value += new Blob([content]).size
        originalAppend(content)
      }

      await readSSEStreamWithEvents(response, isStreamingRef, trackingAppend, enhancedSessionOps)

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

  const streamDuration = computed(() => {
    if (!streamStartTime.value) return 0
    const end = isStreaming.value ? Date.now() : (streamStartTime.value + (bytesReceived.value > 0 ? 0 : 0))
    return Math.max(0, ((isStreaming.value ? Date.now() : streamStartTime.value) - streamStartTime.value) / 1000)
  })

  return {
    isStreaming,
    abortController,
    abort,
    streamChat,
    connectionStatus: readonly(connectionStatus),
    lastError: readonly(lastError),
    retryCount: readonly(retryCount),
    streamStartTime: readonly(streamStartTime),
    bytesReceived: readonly(bytesReceived),
  }
}
