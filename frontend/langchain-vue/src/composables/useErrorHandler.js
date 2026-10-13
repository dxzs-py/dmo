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

// window 级监听的注册守卫与句柄：防重复注册，并为 teardownErrorHandler 提供对称清理依据
let windowListenersBound = false
/** @type {Array<[string, (event: Event) => void]>} */
const boundWindowListeners = []

/**
 * 移除 setupErrorHandler 注册的 window 级监听（对称清理，幂等）。
 * 供应用卸载/HMR 与测试收尾调用；未注册时调用为空操作。
 */
export function teardownErrorHandler() {
  for (const [type, handler] of boundWindowListeners.splice(0)) {
    window.removeEventListener(type, handler)
  }
  windowListenersBound = false
}

/**
 * 注册全局错误处理：Vue errorHandler/warnHandler + window 级 unhandledrejection/error 监听。
 * 重复调用不会堆叠 window 监听（模块级守卫）。
 *
 * @param {import('vue').App} app - Vue 应用实例
 * @returns {() => void} 清理函数（即 teardownErrorHandler），调用后移除全部 window 监听
 */
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

  if (!windowListenersBound) {
    const onUnhandledRejection = (event) => {
      logger.error('❌ [Unhandled Promise Rejection]', event.reason)
      trackError({
        type: 'unhandledrejection',
        reason: event.reason?.message || event.reason,
        timestamp: new Date().toISOString(),
      })
    }

    const onWindowError = (event) => {
      if (isExtensionError(event)) {
        logger.debug('⚠️ [Ignored Extension Error]', event.error?.message || event.message)
        event.preventDefault()
        return
      }
      if (event.error) {
        logger.error('❌ [Runtime Error]', event.error)
      }
    }

    window.addEventListener('unhandledrejection', onUnhandledRejection)
    window.addEventListener('error', onWindowError)
    boundWindowListeners.push(['unhandledrejection', onUnhandledRejection], ['error', onWindowError])
    windowListenersBound = true
  }

  // 浏览器扩展宿主（如 Chrome 插件注入的 window.chrome）可能残留 runtime.lastError，
  // 若不清理会导致后续 runtime 调用被误判为失败。仅当存在该 API 时才清理。
  const hasChromeRuntime =
    typeof window.chrome !== 'undefined' && typeof window.chrome.runtime !== 'undefined'
  if (hasChromeRuntime) {
    window.chrome.runtime.lastError = null
  }

  return teardownErrorHandler
}
