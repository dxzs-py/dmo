import { useUserStore } from '@/stores/user'
import settings from '../config/settings'
import { logger } from './logger'
import { toCamelCase, toSnakeCase } from './sessionTransformers'

const SSE_EVENT_HANDLERS = {
  chunk: (parsed, appendFn) => {
    if (parsed.content) appendFn(parsed.content)
  },
  attachment_ids: (parsed, _appendFn, sessionOps) => {
    if (parsed.data && sessionOps.setAttachmentIds) {
      sessionOps.setAttachmentIds(parsed.data)
    }
  },
  attachment_processing: (parsed, _appendFn, sessionOps) => {
    if (parsed.data) {
      sessionOps.setAttachmentProcessing?.(parsed.data)
    }
  },
  sources: (parsed, _appendFn, sessionOps) => {
    if (parsed.data) sessionOps.setSources?.(parsed.data)
  },
  /** 预留接口：计划展示（后端当前未发送，深度研究等场景可能使用） */
  plan: (parsed, _appendFn, sessionOps) => {
    if (parsed.data) sessionOps.setPlan?.(parsed.data)
  },
  /** 预留接口：思维链展示（后端当前发送 reasoning，此接口预留未来扩展） */
  chain_of_thought: (parsed, _appendFn, sessionOps) => {
    if (parsed.data) sessionOps.setChainOfThought?.(parsed.data)
  },
  tool: (parsed, _appendFn, sessionOps) => {
    if (parsed.data) {
      // 统一 autoApproved → isAutoApproved（P3-12/P3-24 根因修复）：
      // 后端 SSE 事件字段为 auto_approved，toCamelCase 后为 autoApproved，
      // 但 ToolCallCard 读取 toolCall.isAutoApproved（与 WebSocket 路径 toolCallHandler.js 一致）。
      // SSE 路径直接合并 parsed.data 到 toolCall，若不统一字段名，
      // 触发浏览器的"自动通过"徽章不显示（非触发浏览器通过 WebSocket 路径正常显示）。
      if (parsed.data.autoApproved !== undefined && parsed.data.isAutoApproved === undefined) {
        parsed.data.isAutoApproved = parsed.data.autoApproved
        delete parsed.data.autoApproved
      }
      // Task 5.1：SSE tool 事件与 WebSocket tool_call_pending/waiting/running 共用同一
      // 幂等写入入口（sessionStore.addOrUpdateToolCall → addOrUpdateToolCallInMap，
      // id/toolCallId 精确匹配），双通道重复推送同一工具时幂等合并，杜绝重复条目。
      // 保留 SSE 写入：请求浏览器需即时渲染工具卡片，WS 事件存在网络延迟。
      sessionOps.addOrUpdateToolCall?.(parsed.data)
    }
  },
  tool_result: (parsed, _appendFn, sessionOps) => {
    if (parsed.data) {
      // Task 5.1：同上，SSE tool_result 与 WebSocket tool_call_completed/failed/timeout
      // 共用同一幂等写入入口（updateOrAddToolResultInMap），双通道重复推送不重复写入。
      sessionOps.updateOrAddToolResult?.(parsed.data)
    }
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
      const usageData = {}
      if (parsed.data.model) usageData.model = parsed.data.model
      if (parsed.data.totalTokens) usageData.tokenCount = parsed.data.totalTokens
      if (parsed.data.tokens) usageData.tokens = parsed.data.tokens
      if (parsed.data.tokenDetail) usageData.tokenDetail = parsed.data.tokenDetail
      if (parsed.data.responseTime) usageData.responseTime = parsed.data.responseTime
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
    const errorCode = parsed.code || ''
    const errorMsg = parsed.message || parsed.error || '未知错误'
    // 后端 sse_error_event 统一发送 type:"error"，通过 code 区分审批错误
    if (errorCode === 'approval_error') {
      logger.error('[SSE] 审批错误事件:', errorMsg)
      sessionOps.onApprovalError?.({ message: errorMsg, data: parsed.data || {}, code: errorCode })
    } else {
      logger.error('[SSE] 服务端错误事件:', errorMsg)
      sessionOps.setError?.(errorMsg)
    }
  },
  retry: (parsed, _appendFn, sessionOps) => {
    const data = parsed.data || {}
    const retryMs = parsed.retryAfter || (data.backoff ? data.backoff * 1000 : 1000)
    logger.info(`[SSE] 服务端重试通知: ${data.attempt}/${data.max}, ${retryMs}ms 后, 错误: ${data.errorCode}`)
    sessionOps.onRetry?.({
      attempt: data.attempt,
      max: data.max,
      backoff: data.backoff,
      errorCode: data.errorCode,
    })
  },
  timeout_warning: (parsed, _appendFn, sessionOps) => {
    const data = parsed.data || {}
    const elapsed = data.elapsed || '?'
    const limit = data.limit || '?'
    logger.info(`[SSE] 执行超时警告: 已执行 ${elapsed}s, 阈值 ${limit}s`)
    sessionOps.onTimeoutWarning?.({ elapsed, limit })
  },
  approval_timeout: (parsed, _appendFn, sessionOps) => {
    // 兼容两种数据结构：
    // 1. 嵌套：{ type: "approval_timeout", data: { toolName, interruptId, ... } }
    // 2. 扁平：{ type: "approval_timeout", toolName, interruptId, ... }（后端统一事件结构后）
    const data = parsed.data || parsed
    logger.info(`[SSE] 深度研究审批超时: tool=${data.toolName}, interruptId=${data.interruptId}`)
    sessionOps.onApprovalTimeout?.(data)
  },
  heartbeat: (_parsed, _appendFn, _sessionOps) => {
    logger.debug('[SSE] 收到心跳')
  },
  metadata: (parsed, _appendFn, sessionOps) => {
    if (parsed.data) sessionOps.setMetadata?.(parsed.data)
  },
  deep_research: (parsed, _appendFn, sessionOps) => {
    if (parsed.data) sessionOps.setDeepResearchTask?.(parsed.data)
  },
  research_task_id: (parsed, _appendFn, sessionOps) => {
    if (parsed.data) sessionOps.setResearchTaskId?.(parsed.data.researchTaskId)
  },
  model_fallback: (parsed, _appendFn, sessionOps) => {
    if (parsed.data) sessionOps.setModelFallback?.(parsed.data)
  },
  approval: (parsed, _appendFn, sessionOps) => {
    // 兼容嵌套 { type: "approval", data: {...} } 和扁平 { type: "approval", ... } 结构
    const data = parsed.data || parsed
    if (data && Object.keys(data).length > 1) sessionOps.setApproval?.(data)
  },
  /** 审批已处理通知：一端审批后通知另一端更新 UI */
  approval_processed: (parsed, _appendFn, sessionOps) => {
    const data = parsed.data || parsed
    if (data?.interruptId) {
      sessionOps.onApprovalProcessed?.(data)
    }
  },
  /** 历史审批补偿：SSE 重连时后端推送 Redis List 中的历史审批 */
  approval_history: (parsed, _appendFn, sessionOps) => {
    // 传递完整 parsed 对象（含 taskId），而非仅 parsed.data
    // chat.js onApprovalHistory 回调需要 parsed.taskId
    if (parsed.data) sessionOps.onApprovalHistory?.(parsed)
  },
  /** 工具调用去重：短时相同内容自动跳过 */
  tool_usage_dedup: (parsed, _appendFn, sessionOps) => {
    logger.info('[SSE] 工具调用去重:', parsed.data?.message || '')
    sessionOps.onToolUsageDedup?.(parsed.data)
  },
  /** 工具调用阻断：打磨循环/速率超限等被系统阻断 */
  tool_usage_blocked: (parsed, _appendFn, sessionOps) => {
    logger.warn('[SSE] 工具调用阻断:', parsed.data?.message || '')
    sessionOps.onToolUsageBlocked?.(parsed.data)
  },
  /** 工具使用警告：频率接近阈值等 */
  tool_usage_warning: (parsed, _appendFn, sessionOps) => {
    logger.info('[SSE] 工具使用警告:', parsed.data?.message || '')
    sessionOps.onToolUsageWarning?.(parsed.data)
  },
}

/** 
 * 解析 SSE 原始事件（snake_case）为前端 camelCase 并路由分发
 *
 * 命名边界：对 parsed 整体调用 toCamelCase 递归转换，
 * 然后将 type 还原为后端原始 snake_case（协议路由标识符，非业务数据）。
 * 其余字段（parsed.data 嵌套数据 + retry_after/content 等顶层字段）均为前端 camelCase。
 */
export function parseSSEEvent(parsed, appendFn, sessionOps) {
  const camelParsed = toCamelCase(parsed)
  camelParsed.type = parsed.type  // 还原协议标识符

  const handler = SSE_EVENT_HANDLERS[camelParsed.type]
  if (handler) {
    handler(camelParsed, appendFn, sessionOps)
  } else if (camelParsed.content) {
    // 未注册的事件类型携带 content，安全追加但记录警告
    logger.warn(`[SSE] 未注册事件类型 "${camelParsed.type}" 携带 content，已作为文本追加`)
    appendFn(camelParsed.content)
  } else if (camelParsed.type && camelParsed.type !== 'heartbeat') {
    logger.debug(`[SSE] 未注册事件类型 "${camelParsed.type}" 被忽略`)
  }
}

const MAX_RETRIES = 3
const RETRY_BASE_DELAY = 1000
const RETRY_MAX_DELAY = 10000
const CONNECTION_TIMEOUT = 60000
const STREAM_IDLE_TIMEOUT = 180000

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
    if (options.body) headers['Content-Type'] = 'application/json'
    return headers
  }

  const buildUrl = (baseUrl) => {
    if (!baseUrl.startsWith('http')) {
      baseUrl = `${settings.apiBaseUrl}${baseUrl}`
    }
    if (options.injectTokenQuery && userStore.token) {
      const separator = baseUrl.includes('?') ? '&' : '?'
      return `${baseUrl}${separator}token=${encodeURIComponent(userStore.token)}`
    }
    return baseUrl
  }

  const fullUrl = buildUrl(url)
  let lastError = null

  const fetchOptions = {
    method: options.method || (options.body ? 'POST' : 'GET'),
    mode: 'cors',
    credentials: 'include',
    ...options,
  }
  // Remove custom options that are not native fetch options
  delete fetchOptions.maxRetries
  delete fetchOptions.onRetry
  delete fetchOptions.timeout
  delete fetchOptions.injectTokenQuery

  // Task 6.3：幂等键感知——在 body 转 snake_case 之前解析原始 camelCase 字段。
  // 重试仅限"确认未受理"的场景：网络错误（请求未发出）必然未受理，可重试；
  // 携带 clientMessageId 时后端按 id 去重（已受理返回 409），此时 5xx 重试安全；
  // 无幂等键时禁止 5xx 盲重试（重发可能重复创建会话/消息）。
  let hasClientMessageId = false
  if (fetchOptions.body && typeof fetchOptions.body === 'string') {
    try {
      const parsed = JSON.parse(fetchOptions.body)
      hasClientMessageId = typeof parsed.clientMessageId === 'string' && parsed.clientMessageId.length > 0
    } catch { /* body is not JSON, ignore */ }
  }

  // Convert request body from camelCase to snake_case (bypasses axios interceptor)
  if (fetchOptions.body && typeof fetchOptions.body === 'string') {
    try {
      const parsed = JSON.parse(fetchOptions.body)
      fetchOptions.body = JSON.stringify(toSnakeCase(parsed))
    } catch { /* body is not JSON, leave as-is */ }
  }

  for (let attempt = 0; attempt <= (options.maxRetries ?? MAX_RETRIES); attempt++) {
    try {
      const controller = new AbortController()
      const timeoutId = setTimeout(() => controller.abort(), options.timeout ?? CONNECTION_TIMEOUT)

      if (options.signal) {
        options.signal.addEventListener('abort', () => controller.abort(), { once: true })
      }

      const requestOpts = {
        ...fetchOptions,
        signal: controller.signal,
        headers: {
          ...buildHeaders(),
          ...options.headers,
        },
      }

      let response = await fetch(fullUrl, requestOpts)

      clearTimeout(timeoutId)

      if (response.status === 401 && userStore.refreshToken) {
        const refreshed = await userStore.refreshAccessToken()
        if (refreshed) {
          const retryUrl = buildUrl(url)
          const retryOpts = {
            ...fetchOptions,
            signal: controller.signal,
            headers: {
              ...buildHeaders(),
              ...options.headers,
            },
          }
          response = await fetch(retryUrl, retryOpts)
        }
      }

      // Task 6.3：携带 clientMessageId 时，后端对已受理的重复请求返回 409，
      // 说明同一请求已开流，此时重试只会徒增双流风险，直接停止并抛出。
      // __serverResponse 标记使 catch 不再重发（已受理 = 不重发）。
      if (hasClientMessageId && response.status === 409) {
        lastError = new Error('该消息已在处理中，请勿重复发送')
        lastError.__serverResponse = true
        logger.warn('[SSE] 请求已被后端受理（409 duplicate），停止重试')
        throw lastError
      }

      // 5xx：仅当请求携带幂等键 clientMessageId 时才允许重试（后端按 id 去重，
      // 重试安全）；无幂等键时禁止盲重试——重发可能重复创建会话/消息
      // （Task 6.3：仅在确认未受理时重发，服务端已响应一律不盲发）。
      if (response.status >= 500 && hasClientMessageId && attempt < (options.maxRetries ?? MAX_RETRIES)) {
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
            try {
              const parsed = JSON.parse(body)
              const parts = [parsed.message || parsed.error || '']
              if (parsed.data && typeof parsed.data === 'object') {
                const fieldErrors = Object.entries(parsed.data)
                  .map(([field, msgs]) => `${field}: ${Array.isArray(msgs) ? msgs.join(', ') : msgs}`)
                  .join('; ')
                if (fieldErrors) parts.push(fieldErrors)
              }
              errorMsg = parts.filter(Boolean).join(' - ') || errorMsg
            } catch {
              const sseMatch = body.match(/data:\s*(.*)/)
              if (sseMatch) {
                const parsed = JSON.parse(sseMatch[1])
                errorMsg = parsed.message || parsed.error || errorMsg
              }
            }
          }
        } catch {}
        // Task 6.3：服务端已响应（4xx/5xx）说明请求已被受理处理，
        // 标记 __serverResponse 使 catch 不再重发。
        const serverError = new Error(errorMsg)
        serverError.__serverResponse = true
        throw serverError
      }

      return response
    } catch (error) {
      if (error.name === 'AbortError') throw error
      lastError = error
      // Task 6.3：服务端已响应（含 409 已受理 / 4xx 校验失败 / 无幂等键时的 5xx）
      // 一律不再重发；仅网络层错误（请求未发出、必然未受理）允许重试，
      // 重试次数上限为 options.maxRetries ?? MAX_RETRIES。
      if (error.__serverResponse) break
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
