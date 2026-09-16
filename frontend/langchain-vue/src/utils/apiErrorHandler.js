import { ElMessage, ElNotification } from 'element-plus'
import { logger } from './logger'

const ERROR_MESSAGES = {
  400: '请求参数有误，请检查输入内容',
  401: '登录已过期，请重新登录',
  403: '权限不足，无法执行此操作',
  404: '请求的资源不存在',
  405: '请求方法不被允许',
  413: '提交的数据过大，请减少数据量',
  422: '数据验证失败',
  429: '请求过于频繁，请稍后再试',
  500: '服务器内部错误，请稍后重试',
  502: '网关错误，服务暂时不可用',
  503: '服务暂时不可用，请稍后重试',
}

// 业务错误码映射：与后端 common/error_codes.py ErrorCode 枚举一一对应。
// 仅作 data.message 缺失时的兜底文案；后端 message 携带业务上下文，优先展示。
const BIZ_ERROR_CODES = {
  40001: '请求参数错误，请检查输入',
  40002: '数据验证失败，请检查输入',
  40003: '验证码错误或已过期，请刷新后重试',
  40004: '错误的请求',
  40005: '数据已存在',
  40101: '未认证或认证已过期，请重新登录',
  40102: '登录已过期，请重新登录',
  40103: '认证信息无效，请重新登录',
  40104: '请先登录',
  40105: '认证失败，请重新登录',
  40106: '登录失败，请检查用户名和密码',
  40301: '无权访问该资源',
  40302: '权限不足，无法执行此操作',
  40401: '请求的资源不存在',
  40402: '请求的资源不存在',
  40501: '请求方法不允许',
  40901: '资源已存在',
  40902: '工作流已结束，无法提交答案',
  42901: '请求过于频繁，请稍后再试',
  42902: '请求次数超限，请稍后再试',
  42903: '智能体调用频率超限，请稍后再试',
  42904: '账号已锁定，请稍后重试',
  50001: '服务器内部错误，请稍后重试',
  50002: '服务器内部错误，请稍后重试',
  50003: '智能体执行失败，请稍后重试',
  50301: '服务暂不可用，请稍后重试',
}

export function extractErrorMessage(error) {
  if (error.response?.data) {
    const data = error.response.data
    if (data.message) return data.message
    if (data.code && BIZ_ERROR_CODES[data.code]) return BIZ_ERROR_CODES[data.code]
    if (data.error) return data.error
    if (data.detail) return data.detail
    if (data.msg) return data.msg

    if (data.errors && typeof data.errors === 'object') {
      const validationDetails = Object.entries(data.errors)
        .map(([field, messages]) => `${field}: ${Array.isArray(messages) ? messages.join(', ') : messages}`)
        .join('; ')
      if (validationDetails) return `验证失败 (${validationDetails})`
    }

    if (data.nonFieldErrors?.length > 0) {
      return data.nonFieldErrors.join('; ')
    }
  }

  if (error.message) {
    if (error.message.includes('timeout')) return '请求超时，请检查网络连接或稍后重试'
    if (error.message.includes('Network Error') || error.message.includes('Failed to fetch')) return '网络连接失败，请检查网络设置'
    return error.message
  }

  return ERROR_MESSAGES[error.status] || '未知错误，请稍后重试'
}

/**
 * 从 SSE 错误响应中提取错误信息（fetchSSE 与视图层 !response.ok 分支统一入口）
 *
 * 根因修复：Response body 只能读取一次，旧实现先 json() 失败后再 text()
 * 必然得到空串，SSE data 行回退路径实际失效。此处改为 text() 单次读取，
 * 再依次尝试整体 JSON 解析与 SSE data 行回退解析。
 *
 * 提取优先级：message → error；errors 校验详情追加 (...)；
 * data 对象详情追加 [...]；均无时回退默认 HTTP 状态描述。
 *
 * @param {Response} response - fetch Response（status 非 2xx）
 * @returns {Promise<Error>} 携带提取后错误信息的 Error 实例
 */
export async function extractSSEError(response) {
  let errorMsg = `HTTP ${response.status}`

  let text
  try {
    text = await response.text()
  } catch (err) {
    logger.warn('[API] 读取 SSE 错误响应体失败:', err)
    return new Error(errorMsg)
  }

  let parsed = null
  if (text.trim()) {
    try {
      parsed = JSON.parse(text)
    } catch {
      // 响应体非 JSON 时尝试按 SSE data 行提取错误信息
      const sseMatch = text.match(/data:\s*(.*)/)
      if (sseMatch) {
        try {
          parsed = JSON.parse(sseMatch[1])
        } catch (sseErr) {
          logger.warn('[API] 错误响应 SSE 数据解析失败:', sseErr)
        }
      }
    }
  }

  if (parsed && typeof parsed === 'object') {
    if (parsed.message) {
      errorMsg = parsed.message
    } else if (parsed.error) {
      errorMsg = parsed.error
    }
    if (parsed.errors && typeof parsed.errors === 'object') {
      const validationDetails = Object.entries(parsed.errors)
        .map(([field, messages]) => `${field}: ${Array.isArray(messages) ? messages.join(', ') : messages}`)
        .join('; ')
      if (validationDetails) {
        errorMsg += ` (${validationDetails})`
      }
    }
    if (parsed.data && typeof parsed.data === 'object' && !Array.isArray(parsed.data)) {
      const dataStr = Object.values(parsed.data).map(d => {
        if (typeof d === 'string') return d
        if (Array.isArray(d)) return d.join(', ')
        return JSON.stringify(d)
      }).join(', ')
      if (dataStr) {
        errorMsg += ` [${dataStr}]`
      }
    }
  }

  return new Error(errorMsg)
}

export async function handleApiError(error, options = {}) {
  const { showToast = true, showNotification = false, customMessage = null } = options
  const status = error.response?.status || error.status || 0
  const message = customMessage || extractErrorMessage(error)

  logger.error('[API Error]', {
    status,
    url: error.config?.url || error.request?.responseURL || 'unknown',
    message,
    details: error.response?.data,
    stack: error.stack,
  })

  if (status === 401) {
    try {
      const { useUserStore } = await import('../stores/user.js')
      const userStore = useUserStore()
      if (userStore?.logout) {
        userStore.logout()
        setTimeout(() => {
          window.location.href = '/login'
        }, 100)
      }
    } catch {
      window.location.href = '/login'
    }
  }

  if (showToast) {
    ElMessage.error(message)
  }

  if (showNotification) {
    ElNotification.error({
      title: '操作失败',
      message,
      duration: 5000,
    })
  }

  return {
    success: false,
    error: {
      status,
      message,
      original: error,
    }
  }
}

export function handleValidationError(errors, context = '') {
  const errorMessages = []

  if (Array.isArray(errors)) {
    errors.forEach(err => {
      if (typeof err === 'string') {
        errorMessages.push(err)
      } else if (err.field && err.message) {
        errorMessages.push(`${err.field}: ${err.message}`)
      } else if (err.message) {
        errorMessages.push(err.message)
      }
    })
  } else if (typeof errors === 'object' && errors !== null) {
    Object.entries(errors).forEach(([field, value]) => {
      if (Array.isArray(value)) {
        value.forEach(msg => errorMessages.push(`${field}: ${msg}`))
      } else if (typeof value === 'string') {
        errorMessages.push(`${field}: ${value}`)
      } else if (value?.message) {
        errorMessages.push(`${field}: ${value.message}`)
      }
    })
  }

  if (context) {
    errorMessages.unshift(`[${context}]`)
  }

  const fullMessage = errorMessages.join('\n')

  ElMessage.error(fullMessage)

  return fullMessage
}

export function createErrorHandler(componentName = '') {
  return (error, customOptions = {}) => {
    return handleApiError(error, {
      ...customOptions,
      showNotification: componentName ? true : false,
    })
  }
}

export function wrapWithErrorHandling(asyncFn, options = {}) {
  return async (...args) => {
    try {
      return await asyncFn(...args)
    } catch (error) {
      handleApiError(error, options)
      throw error
    }
  }
}

export default {
  handleApiError,
  handleValidationError,
  createErrorHandler,
  wrapWithErrorHandling,
  ERROR_MESSAGES,
  BIZ_ERROR_CODES,
}
