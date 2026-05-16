import { useUserStore } from '@/stores/user'
import settings from '../config/settings'
import { logger } from './logger'

const SSE_EVENT_HANDLERS = {
  chunk: (parsed, appendFn) => {
    if (parsed.content) appendFn(parsed.content)
  },
  attachment_ids: (parsed, _appendFn, sessionOps) => {
    if (parsed.data && sessionOps.setAttachmentIds) {
      sessionOps.setAttachmentIds(parsed.data)
    }
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
    if (parsed.data) {
      sessionOps.addOrUpdateToolCall?.(parsed.data)
    }
  },
  tool_result: (parsed, _appendFn, sessionOps) => {
    if (parsed.data) {
      sessionOps.updateOrAddToolResult?.(parsed.data)
    }
  },
  tool_confirmation: (parsed, _appendFn, sessionOps) => {
    if (parsed.data) sessionOps.requestToolConfirmation?.(parsed.data)
  },
  reasoning: (parsed, _appendFn, sessionOps) => {
    if (parsed.data) sessionOps.setReasoning?.(parsed.data)
  },
  suggestions: (parsed, _appendFn, sessionOps) => {
    if (parsed.data) sessionOps.setSuggestions?.(parsed.data)
  },
  context: (parsed, _appendFn, sessionOps) => {
    if (parsed.data) {
      sessionOps.setContext?.(parsed.data)
      if (parsed.data.cost) {
        sessionOps.setCost?.(parsed.data.cost)
      }
      const usageData = {}
      if (parsed.data.model) usageData.model = parsed.data.model
      if (parsed.data.total_tokens) usageData.tokenCount = parsed.data.total_tokens
      if (parsed.data.cost?.totalCost !== undefined) usageData.cost = parsed.data.cost.totalCost
      if (parsed.data.response_time) usageData.responseTime = parsed.data.response_time
      if (Object.keys(usageData).length > 0) {
        sessionOps.setUsage?.(usageData)
      }
    }
  },
  command: (parsed, _appendFn, sessionOps) => {
    if (parsed.data) {
      sessionOps.setContext?.({ command: parsed.data })
    }
  },
  error: (parsed, _appendFn, sessionOps) => {
    const errorMsg = parsed.message || parsed.error || '未知错误'
    logger.error('[SSE] 服务端错误事件:', errorMsg)
    sessionOps.setError?.(errorMsg)
  },
  retry: (parsed, _appendFn, sessionOps) => {
    const retryMs = parsed.retry_after || 1000
    logger.info(`[SSE] 服务端建议重试, ${retryMs}ms 后`)
    sessionOps.scheduleRetry?.(retryMs)
  },
  heartbeat: (_parsed, _appendFn, _sessionOps) => {
    logger.debug('[SSE] 收到心跳')
  },
  metadata: (parsed, _appendFn, sessionOps) => {
    if (parsed.data) sessionOps.setMetadata?.(parsed.data)
  },
}

export function parseSSEEvent(parsed, appendFn, sessionOps) {
  const handler = SSE_EVENT_HANDLERS[parsed.type]
  if (handler) {
    handler(parsed, appendFn, sessionOps)
  } else if (parsed.content) {
    appendFn(parsed.content)
  }
}

const MAX_RETRIES = 3
const RETRY_BASE_DELAY = 1000
const RETRY_MAX_DELAY = 10000
const CONNECTION_TIMEOUT = 30000
const STREAM_IDLE_TIMEOUT = 60000

function getRetryDelay(attempt) {
  const delay = RETRY_BASE_DELAY * Math.pow(2, attempt)
  const jitter = Math.random() * 0.3 * delay
  return Math.min(delay + jitter, RETRY_MAX_DELAY)
}

export async function fetchSSE(url, options = {}) {
  const userStore = useUserStore()
  const buildHeaders = () => {
    const headers = { 'Accept': 'text/event-stream' }
    if (userStore.token) headers['Authorization'] = `Bearer ${userStore.token}`
    return headers
  }

  const fullUrl = url.startsWith('http') ? url : `${settings.API_BASE_URL}${url}`
  let lastError = null

  for (let attempt = 0; attempt <= (options.maxRetries ?? MAX_RETRIES); attempt++) {
    try {
      const controller = new AbortController()
      const timeoutId = setTimeout(() => controller.abort(), options.timeout ?? CONNECTION_TIMEOUT)

      if (options.signal) {
        options.signal.addEventListener('abort', () => controller.abort(), { once: true })
      }

      let response = await fetch(fullUrl, {
        method: 'GET',
        mode: 'cors',
        credentials: 'include',
        ...options,
        signal: controller.signal,
        headers: {
          ...buildHeaders(),
          ...options.headers,
        },
      })

      clearTimeout(timeoutId)

      if (response.status === 401 && userStore.refreshToken) {
        const refreshed = await userStore.refreshAccessToken()
        if (refreshed) {
          response = await fetch(fullUrl, {
            method: 'GET',
            mode: 'cors',
            credentials: 'include',
            ...options,
            headers: {
              ...buildHeaders(),
              ...options.headers,
            },
          })
        }
      }

      if (response.status >= 500 && attempt < (options.maxRetries ?? MAX_RETRIES)) {
        lastError = new Error(`服务器错误: HTTP ${response.status}`)
        const delay = getRetryDelay(attempt)
        logger.warn(`[SSE] 服务器错误 ${response.status}, ${delay}ms 后重试 (${attempt + 1}/${MAX_RETRIES})`)
        options.onRetry?.(attempt + 1, MAX_RETRIES, delay)
        await new Promise(resolve => setTimeout(resolve, delay))
        continue
      }

      if (!response.ok) {
        let errorMsg = `HTTP ${response.status}`
        try {
          const reader = response.body?.getReader()
          if (reader) {
            const decoder = new TextDecoder()
            let body = ''
            while (true) {
              const { done, value } = await reader.read()
              if (done) break
              body += decoder.decode(value, { stream: true })
            }
            const sseMatch = body.match(/data:\s*(.*)/)
            if (sseMatch) {
              const parsed = JSON.parse(sseMatch[1])
              errorMsg = parsed.message || parsed.error || errorMsg
            }
          }
        } catch {}
        throw new Error(errorMsg)
      }

      return response
    } catch (error) {
      if (error.name === 'AbortError') throw error
      lastError = error
      if (attempt < (options.maxRetries ?? MAX_RETRIES)) {
        const delay = getRetryDelay(attempt)
        logger.warn(`[SSE] 请求失败: ${error.message}, ${delay}ms 后重试 (${attempt + 1}/${MAX_RETRIES})`)
        options.onRetry?.(attempt + 1, MAX_RETRIES, delay)
        await new Promise(resolve => setTimeout(resolve, delay))
      }
    }
  }

  throw lastError || new Error('SSE 请求失败，已达最大重试次数')
}

export async function readSSEStream(response, onEvent, signal) {
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let lastEventTime = Date.now()

  const idleCheckInterval = setInterval(() => {
    if (Date.now() - lastEventTime > STREAM_IDLE_TIMEOUT) {
      logger.warn('[SSE] 流空闲超时，关闭连接')
      clearInterval(idleCheckInterval)
      reader.cancel().catch(() => {})
    }
  }, 10000)

  try {
    while (true) {
      if (signal && signal.aborted) break

      const { done, value } = await reader.read()
      if (done) break

      lastEventTime = Date.now()
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        const dataStr = line.slice(6)
        if (dataStr === '[DONE]') continue

        try {
          const parsed = JSON.parse(dataStr)
          onEvent(parsed)
        } catch (e) {
          logger.warn('[SSE] 解析数据失败:', dataStr, e)
        }
      }
    }

    if (buffer.trim() && buffer.startsWith('data: ')) {
      const dataStr = buffer.slice(6)
      if (dataStr.trim() && dataStr !== '[DONE]') {
        try {
          const parsed = JSON.parse(dataStr)
          onEvent(parsed)
        } catch {}
      }
    }
  } finally {
    clearInterval(idleCheckInterval)
    reader.releaseLock()
  }
}

export async function readSSEStreamWithEvents(response, isStreamingRef, appendFn, sessionOps) {
  await readSSEStream(response, (parsed) => {
    if (isStreamingRef && !isStreamingRef.value) return
    parseSSEEvent(parsed, appendFn, sessionOps)
  })
}

export { MAX_RETRIES, RETRY_BASE_DELAY, RETRY_MAX_DELAY, CONNECTION_TIMEOUT, STREAM_IDLE_TIMEOUT }
