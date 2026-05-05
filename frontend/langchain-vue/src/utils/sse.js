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
    if (parsed.data) sessionOps.addToolCall?.(parsed.data)
  },
  tool_result: (parsed, _appendFn, sessionOps) => {
    if (parsed.data) sessionOps.addToolCall?.(parsed.data)
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
}

export function parseSSEEvent(parsed, appendFn, sessionOps) {
  const handler = SSE_EVENT_HANDLERS[parsed.type]
  if (handler) {
    handler(parsed, appendFn, sessionOps)
  } else if (parsed.content) {
    appendFn(parsed.content)
  }
}

export async function fetchSSE(url, options = {}) {
  const userStore = useUserStore()
  const buildHeaders = () => {
    const headers = { 'Accept': 'text/event-stream' }
    if (userStore.token) headers['Authorization'] = `Bearer ${userStore.token}`
    return headers
  }

  const fullUrl = url.startsWith('http') ? url : `${settings.API_BASE_URL}${url}`

  let response = await fetch(fullUrl, {
    method: 'GET',
    mode: 'cors',
    credentials: 'include',
    ...options,
    headers: {
      ...buildHeaders(),
      ...options.headers,
    },
  })

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
}

export async function readSSEStream(response, onEvent, signal) {
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      if (signal && signal.aborted) break

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
    reader.releaseLock()
  }
}

export async function readSSEStreamWithEvents(response, isStreamingRef, appendFn, sessionOps) {
  await readSSEStream(response, (parsed) => {
    if (isStreamingRef && !isStreamingRef.value) return
    parseSSEEvent(parsed, appendFn, sessionOps)
  })
}
