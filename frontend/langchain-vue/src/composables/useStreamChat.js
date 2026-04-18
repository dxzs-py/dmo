import { ref } from 'vue'
import { chatAPI } from '../api/client'

/**
 * 解析后端返回的错误信息，支持验证错误的详细字段展示
 */
function parseBackendError(response) {
  return async () => {
    let errorMsg = `HTTP ${response.status}`
    try {
      const errBody = await response.json()

      if (errBody.message) {
        errorMsg = errBody.message
      } else if (errBody.error) {
        errorMsg = errBody.error
      }

      // 处理验证错误（errors 字段，对象格式）
      if (errBody.errors && typeof errBody.errors === 'object') {
        const validationDetails = Object.entries(errBody.errors)
          .map(([field, messages]) => `${field}: ${Array.isArray(messages) ? messages.join(', ') : messages}`)
          .join('; ')
        if (validationDetails) {
          errorMsg += ` (${validationDetails})`
        }
      }

      // 处理 details 对象格式（DRF 验证错误: { field_name: [...] }）
      if (errBody.details && typeof errBody.details === 'object' && !Array.isArray(errBody.details)) {
        const detailStr = Object.entries(errBody.details)
          .map(([field, items]) => {
            if (Array.isArray(items)) {
              const itemErrors = items
                .filter(item => item && typeof item === 'object')
                .flatMap(item => Object.entries(item).map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(', ') : v}`))
              return itemErrors.length > 0 ? `${field}[${itemErrors.join('|')}]` : null
            }
            return `${field}: ${items}`
          })
          .filter(Boolean)
          .join('; ')
        if (detailStr) {
          errorMsg += ` [${detailStr}]`
        }
      }

      // 处理 details 数组格式（通用错误列表）
      if (errBody.details && Array.isArray(errBody.details)) {
        const detailsStr = errBody.details.map(d => d.message || d.msg || JSON.stringify(d)).join(', ')
        if (detailsStr) {
          errorMsg += ` [${detailsStr}]`
        }
      }
    } catch (error) {
      console.warn('解析错误响应失败:', error)
    }
    return new Error(errorMsg)
  }
}

const SSE_EVENT_HANDLERS = {
  chunk: (parsed, appendFn) => {
    if (parsed.content) appendFn(parsed.content)
  },
  source: (parsed, _appendFn, sessionOps) => {
    if (parsed.data) sessionOps.addSource?.(parsed.data)
  },
  sources: (parsed, _appendFn, sessionOps) => {
    if (parsed.data) sessionOps.setSources?.(parsed.data)
  },
  plan: (parsed, _appendFn, sessionOps) => {
    if (parsed.data) sessionOps.setPlan?.(parsed.data)
  },
  chainOfThought: (parsed, _appendFn, sessionOps) => {
    if (parsed.data) sessionOps.setChainOfThought?.(parsed.data)
  },
  tool: (parsed, _appendFn, sessionOps) => {
    if (parsed.data) sessionOps.addToolCall?.(parsed.data)
  },
  tool_result: (parsed, _appendFn, sessionOps) => {
    if (parsed.data) sessionOps.addToolCall?.(parsed.data)
  },
  reasoning: (parsed, _appendFn, sessionOps) => {
    if (parsed.data) sessionOps.setReasoning?.(parsed.data)
  },
  suggestions: (parsed, _appendFn, sessionOps) => {
    if (parsed.data) sessionOps.setSuggestions?.(parsed.data)
  },
  context: (parsed, _appendFn, sessionOps) => {
    if (parsed.data) sessionOps.setContext?.(parsed.data)
  },
}

function parseSSEEvent(parsed, appendFn, sessionOps) {
  const handler = SSE_EVENT_HANDLERS[parsed.type]
  if (handler) {
    handler(parsed, appendFn, sessionOps)
  } else if (parsed.content) {
    appendFn(parsed.content)
  }
}

async function readSSEStream(response, isStreamingRef, appendFn, sessionOps) {
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      if (!isStreamingRef.value) break
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        const dataStr = line.slice(6)
        if (dataStr === '[DONE]') continue

        try {
          const parsed = JSON.parse(dataStr)
          parseSSEEvent(parsed, appendFn, sessionOps)
        } catch (error) {
          console.warn('解析 SSE 数据失败:', dataStr, error)
        }
      }
    }
  } finally {
    reader.releaseLock()
  }
}

export function useStreamChat() {
  const isStreaming = ref(false)
  const abortController = ref(null)

  function abort() {
    if (abortController.value) {
      try {
        abortController.value.abort()
      } catch (error) {
        console.warn('中止请求失败:', error)
      }
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
        const parseError = parseBackendError(response)
        const error = await parseError()
        console.error('[StreamChat] 流式请求失败:', response.status, error.message)
        throw error
      }

      await readSSEStream(response, isStreamingRef, appendFn, sessionOps)
      return { success: true }
    } catch (error) {
      if (error.name === 'AbortError') return { success: false, aborted: true }
      console.error('[StreamChat] 异常:', error.message)
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

export { parseSSEEvent, readSSEStream }
