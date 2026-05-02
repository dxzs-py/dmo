import { ref } from 'vue'
import { chatAPI } from '../api'
import { readSSEStreamWithEvents } from '../utils/sse'
import { extractSSEError } from '../utils/api-error-handler'
import { logger } from '../utils/logger'

export function useStreamChat() {
  const isStreaming = ref(false)
  const abortController = ref(null)

  function abort() {
    if (abortController.value) {
      abortController.value.abort()
    }
    isStreaming.value = false
    abortController.value = null
  }

  async function streamChat(requestData, sessionOps) {
    const controller = new AbortController()
    abortController.value = controller
    isStreaming.value = true

    const appendFn = sessionOps.appendToLastMessage
    const isStreamingRef = isStreaming

    try {
      const response = await chatAPI.streamMessage(requestData, {
        signal: controller.signal,
      })

      if (!response.ok) {
        const error = await extractSSEError(response)
        logger.error('[StreamChat] 流式请求失败:', response.status, error.message)
        throw error
      }

      await readSSEStreamWithEvents(response, isStreamingRef, appendFn, sessionOps)
      return { success: true }
    } catch (error) {
      if (error.name === 'AbortError') return { success: false, aborted: true }
      logger.error('[StreamChat] 异常:', error.message)
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
  }
}
