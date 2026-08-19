import { useUserStore } from '@/stores/user'
import settings from '../config/settings'
import { logger } from './logger'
import { toSnakeCase, toCamelCase } from './sessionTransformers'
import { extractSSEError } from './apiErrorHandler'

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
        // rd-06：错误响应解析统一到 apiErrorHandler.extractSSEError
        // （text() 单次读取 + JSON / SSE data 行回退，格式见其 JSDoc）
        const serverError = await extractSSEError(response)
        // Task 6.3：服务端已响应（4xx/5xx）说明请求已被受理处理，
        // 标记 __serverResponse 使 catch 不再重发。
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

/**
 * 解析原始事件（snake_case）为前端 camelCase 并路由分发
 *
 * 命名边界：对整体对象调用 toCamelCase 递归转换，
 * 然后将 type 与顶层 subagent_thread_id 还原为后端原始 snake_case
 * （协议路由标识符，非业务数据）。其余字段均为前端 camelCase。
 *
 * @param {*} raw - SSE data 行 / WebSocket message JSON.parse 后的原始事件
 * @returns {*} 转换后的事件对象（非普通对象经 toCamelCase 透传/数组逐项转换）
 */
export function parseProtocolEvent(raw) {
  const converted = toCamelCase(raw)
  // 严格判断普通对象（Object.prototype.toString，见 SpecificationRequirements：
  // 禁止用 typeof 判断，防止 Date/RegExp/null 被误处理）
  if (Object.prototype.toString.call(raw) !== '[object Object]') return converted
  if (raw.type) {
    converted.type = raw.type
  }
  if (raw.subagent_thread_id) {
    converted.subagent_thread_id = raw.subagent_thread_id
  }
  return converted
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
        if (dataStr === '[DONE]') break

        try {
          const parsed = JSON.parse(dataStr)
          onEvent(parseProtocolEvent(parsed))
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
          onEvent(parseProtocolEvent(parsed))
        } catch (err) {
          // 流结束时缓冲区残留的半行数据可能不完整，解析失败仅记录，忽略该残片
          logger.warn('[SSE] 解析尾部数据失败:', dataStr, err)
        }
      }
    }
  } finally {
    clearInterval(idleCheckInterval)
    reader.releaseLock()
  }
}

export { MAX_RETRIES, RETRY_BASE_DELAY, RETRY_MAX_DELAY, CONNECTION_TIMEOUT, STREAM_IDLE_TIMEOUT }
