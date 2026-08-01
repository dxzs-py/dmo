/**
 * 异步工具函数（防抖 / 节流 / sleep / retry 等）
 * 纯函数实现，不依赖 Vue 生命周期，可在任何环境复用。
 * Vue 组件内如需自动 onUnmounted 清理，请使用 composables/useDebounce.js 的组合式封装。
 */

/**
 * 防抖：在延迟窗口内重复调用会重置定时器，仅最后一次调用生效
 * @param {Function} fn - 要防抖的函数
 * @param {number} [delay=200] - 延迟毫秒
 * @returns {Function & { cancel: () => void, flush: () => void }} 防抖函数，附带 cancel / flush 方法
 */
export function debounce(fn, delay = 200) {
  /** @type {ReturnType<typeof setTimeout> | null} */
  let timer = null

  const debounced = (...args) => {
    if (timer) clearTimeout(timer)
    timer = setTimeout(() => {
      timer = null
      fn(...args)
    }, delay)
  }

  /** 取消尚未执行的防抖调用 */
  debounced.cancel = () => {
    if (timer) {
      clearTimeout(timer)
      timer = null
    }
  }

  /** 立即执行尚未触发的防抖调用（保留最后一次调用的参数） */
  debounced.flush = () => {
    if (timer) {
      clearTimeout(timer)
      timer = null
      fn()
    }
  }

  return debounced
}

/**
 * 节流：在间隔窗口内最多触发一次，期间重复调用被忽略
 * @param {Function} fn - 要节流的函数
 * @param {number} [interval=100] - 间隔毫秒
 * @returns {Function & { cancel: () => void }} 节流函数，附带 cancel 方法
 */
export function throttle(fn, interval = 100) {
  let lastTime = 0
  /** @type {ReturnType<typeof setTimeout> | null} */
  let timer = null

  const throttled = (...args) => {
    const now = Date.now()
    const remaining = interval - (now - lastTime)
    if (remaining <= 0) {
      if (timer) {
        clearTimeout(timer)
        timer = null
      }
      lastTime = now
      fn(...args)
    } else if (!timer) {
      timer = setTimeout(() => {
        lastTime = Date.now()
        timer = null
        fn(...args)
      }, remaining)
    }
  }

  throttled.cancel = () => {
    if (timer) {
      clearTimeout(timer)
      timer = null
    }
  }

  return throttled
}

/**
 * 延迟指定毫秒
 * @param {number} ms - 延迟毫秒
 * @param {AbortSignal} [signal] - 可选中止信号
 * @returns {Promise<void>}
 */
export function sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException('Aborted', 'AbortError'))
      return
    }
    const timer = setTimeout(resolve, ms)
    signal?.addEventListener('abort', () => {
      clearTimeout(timer)
      reject(new DOMException('Aborted', 'AbortError'))
    }, { once: true })
  })
}

/**
 * 指数退避重试
 * @param {Function} fn - 返回 Promise 的函数，接收 (attempt, signal) 参数
 * @param {Object} [options]
 * @param {number} [options.retries=3] - 最大重试次数
 * @param {number} [options.baseDelay=1000] - 基础延迟毫秒
 * @param {number} [options.maxDelay=10000] - 最大延迟毫秒
 * @param {Function} [options.shouldRetry] - 判断错误是否应重试
 * @returns {Promise<*>}
 */
export async function retry(fn, options = {}) {
  const {
    retries = 3,
    baseDelay = 1000,
    maxDelay = 10000,
    shouldRetry = () => true,
  } = options

  let lastError
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      return await fn(attempt)
    } catch (error) {
      lastError = error
      if (attempt >= retries || !shouldRetry(error)) {
        throw error
      }
      const delay = Math.min(baseDelay * Math.pow(2, attempt), maxDelay)
      await sleep(delay)
    }
  }
  throw lastError
}
