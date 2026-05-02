import { logger } from '../utils/logger'

function isExtensionError(event) {
  return (
    (event.filename && (
      event.filename.includes('content.js') ||
      event.filename.includes('stat-') ||
      event.filename.includes('extension') ||
      event.filename.includes('chrome-extension://')
    )) ||
    (event.error && event.error.stack && (
      event.error.stack.includes('content.js') ||
      event.error.stack.includes('stat-') ||
      event.error.stack.includes('chrome-extension://')
    )) ||
    (event.error && event.error.message && (
      event.error.message.includes('runtime.lastError') ||
      event.error.message.includes('message port closed') ||
      event.error.message.includes('indexOf is not a function')
    ))
  )
}

function trackError(errorDetails) {
  if (import.meta.env.PROD && typeof window.__ERROR_TRACKER__ === 'function') {
    window.__ERROR_TRACKER__(errorDetails)
  }
}

export function setupErrorHandler(app) {
  app.config.errorHandler = (err, instance, info) => {
    const errorDetails = {
      message: err?.message || '未知错误',
      stack: err?.stack,
      component: instance?.$options?.name || instance?.$options?.__name || '未知组件',
      lifecycleHook: info || '未知生命周期',
      timestamp: new Date().toISOString(),
      url: window.location.href,
      userAgent: navigator.userAgent,
    }

    logger.error('❌ [Global Error]', errorDetails)

    if (import.meta.env.DEV) {
      logger.group('🔍 错误详情')
      logger.error('错误对象:', err)
      logger.error('组件实例:', instance)
      logger.error('生命周期:', info)
      logger.groupEnd()
    }

    trackError(errorDetails)
  }

  app.config.warnHandler = (msg, instance, trace) => {
    if (import.meta.env.DEV) {
      logger.warn(`⚠️ [Vue Warning] ${msg}`, { component: instance?.$options?.name, trace })
    }
  }

  window.addEventListener('unhandledrejection', (event) => {
    logger.error('❌ [Unhandled Promise Rejection]', event.reason)
    trackError({
      type: 'unhandledrejection',
      reason: event.reason?.message || event.reason,
      timestamp: new Date().toISOString(),
    })
  })

  window.addEventListener('error', (event) => {
    if (isExtensionError(event)) {
      logger.debug('⚠️ [Ignored Extension Error]', event.error?.message || event.message)
      event.preventDefault()
      return
    }
    if (event.error) {
      logger.error('❌ [Runtime Error]', event.error)
    }
  })

  if (typeof chrome !== 'undefined' && chrome.runtime) {
    chrome.runtime.lastError = null
  }
}
