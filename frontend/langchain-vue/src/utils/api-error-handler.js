import { ElMessage, ElNotification } from 'element-plus'

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

function extractErrorMessage(error) {
  if (error.response?.data) {
    const data = error.response.data

    if (data.message) return data.message
    if (data.error) return data.error
    if (data.detail) return data.detail
    if (data.msg) return data.msg

    if (data.errors && typeof data.errors === 'object') {
      const validationDetails = Object.entries(data.errors)
        .map(([field, messages]) => `${field}: ${Array.isArray(messages) ? messages.join(', ') : messages}`)
        .join('; ')
      if (validationDetails) return `验证失败 (${validationDetails})`
    }

    if (data.non_field_errors?.length > 0) {
      return data.non_field_errors.join('; ')
    }
  }

  if (error.message) {
    if (error.message.includes('timeout')) return '请求超时，请检查网络连接或稍后重试'
    if (error.message.includes('Network Error') || error.message.includes('Failed to fetch')) return '网络连接失败，请检查网络设置'
    return error.message
  }

  return ERROR_MESSAGES[error.status] || '未知错误，请稍后重试'
}

export function handleApiError(error, options = {}) {
  const { showToast = true, showNotification = false, customMessage = null } = options
  const status = error.response?.status || error.status || 0
  const message = customMessage || extractErrorMessage(error)

  console.error('[API Error]', {
    status,
    url: error.config?.url || error.request?.responseURL || 'unknown',
    message,
    details: error.response?.data,
    stack: error.stack,
  })

  if (status === 401) {
    const userStore = window.__pinia?._s?.get('user')
    if (userStore?.logout) {
      userStore.logout()
      setTimeout(() => {
        window.location.href = '/login'
      }, 100)
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

export function withErrorHandling(asyncFn, options = {}) {
  return async (...args) => {
    try {
      return await asyncFn(...args)
    } catch (error) {
      handleApiError(error, options)
      throw error
    }
  }
}

export function validateBeforeRequest(data, rules, context = '') {
  const errors = []

  Object.entries(rules).forEach(([field, rule]) => {
    const value = data[field]

    if (rule.required && (value === undefined || value === null || value === '')) {
      errors.push(`${rule.label || field}不能为空`)
      return
    }

    if (value !== undefined && value !== null && value !== '') {
      if (rule.type && typeof value !== rule.type) {
        errors.push(`${rule.label || field}类型不正确，期望${rule.type}类型`)
      }

      if (rule.maxLength && String(value).length > rule.maxLength) {
        errors.push(`${rule.label || field}长度不能超过${rule.maxLength}个字符`)
      }

      if (rule.minLength && String(value).length < rule.minLength) {
        errors.push(`${rule.label || field}长度不能少于${rule.minLength}个字符`)
      }

      if (rule.pattern && !rule.pattern.test(value)) {
        errors.push(rule.message || `${rule.label || field}格式不正确`)
      }

      if (rule.enum && !rule.enum.includes(value)) {
        errors.push(`${rule.label || field}值无效，允许的值: ${rule.enum.join(', ')}`)
      }

      if (rule.validator) {
        const result = rule.validator(value, data)
        if (result !== true) {
          errors.push(result || `${rule.label || field}验证失败`)
        }
      }
    }
  })

  if (errors.length > 0) {
    if (context) {
      errors.unshift(`[${context}]`)
    }
    handleValidationError(errors)
    return false
  }

  return true
}

export function createRetryHandler(maxRetries = 3, delay = 1000) {
  let retryCount = 0

  return async function retryWrapper(fn, ...args) {
    try {
      const result = await fn(...args)
      retryCount = 0
      return result
    } catch (error) {
      const status = error.response?.status || error.status || 0

      if (
        retryCount < maxRetries &&
        [500, 502, 503, 504].includes(status) &&
        !navigator.onLine === false
      ) {
        retryCount++
        console.warn(`[Retry] Attempt ${retryCount}/${maxRetries} for ${error.config?.url}`)
        await new Promise(resolve => setTimeout(resolve, delay * retryCount))
        return retryWrapper(fn, ...args)
      }

      retryCount = 0
      throw error
    }
  }
}

export default {
  handleApiError,
  handleValidationError,
  createErrorHandler,
  withErrorHandling,
  validateBeforeRequest,
  createRetryHandler,
  ERROR_MESSAGES,
}
