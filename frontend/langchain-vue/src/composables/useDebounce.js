import { ref, onUnmounted } from 'vue'

/**
 * 防抖函数 - 组合式函数
 * @param {Function} fn - 要防抖的函数
 * @param {number} delay - 延迟时间（毫秒）
 * @returns {{ debouncedFn: Function, cancel: Function, flush: Function }}
 * 
 * @example
 * const { debouncedFn, cancel } = useDebounce((value) => {
 *   console.log('搜索:', value)
 * }, 300)
 * 
 * // 在模板中使用
 * <input @input="debouncedFn($event.target.value)" />
 */
export function useDebounce(fn, delay = 300) {
  let timeoutId = null

  const debouncedFn = (...args) => {
    if (timeoutId) clearTimeout(timeoutId)
    timeoutId = setTimeout(() => {
      fn(...args)
      timeoutId = null
    }, delay)
  }

  const cancel = () => {
    if (timeoutId) {
      clearTimeout(timeoutId)
      timeoutId = null
    }
  }

  const flush = () => {
    if (timeoutId) {
      clearTimeout(timeoutId)
      fn()
      timeoutId = null
    }
  }

  onUnmounted(() => cancel())

  return { debouncedFn, cancel, flush }
}

/**
 * 节流函数 - 组合式函数
 * @param {Function} fn - 要节流的函数
 * @param {number} interval - 间隔时间（毫秒）
 * @returns {{ throttledFn: Function, cancel: Function }}
 * 
 * @example
 * const { throttledFn } = useThrottle(() => {
 *   console.log('滚动处理')
 * }, 100)
 */
export function useThrottle(fn, interval = 100) {
  let lastTime = 0
  let timeoutId = null

  const throttledFn = (...args) => {
    const now = Date.now()
    const remaining = interval - (now - lastTime)

    if (remaining <= 0) {
      if (timeoutId) {
        clearTimeout(timeoutId)
        timeoutId = null
      }
      lastTime = now
      fn(...args)
    } else if (!timeoutId) {
      timeoutId = setTimeout(() => {
        lastTime = Date.now()
        timeoutId = null
        fn(...args)
      }, remaining)
    }
  }

  const cancel = () => {
    if (timeoutId) {
      clearTimeout(timeoutId)
      timeoutId = null
    }
  }

  onUnmounted(() => cancel())

  return { throttledFn, cancel }
}

/**
 * 带自动取消的异步请求防抖
 * 用于防止快速连续点击或输入导致的重复API调用
 * 
 * @param {Function} asyncFn - 异步函数
 * @param {number} delay - 防抖延迟（毫秒）
 * @returns {{ execute: Function, cancel: Function, isLoading: import('vue').Ref<boolean> }}
 * 
 * @example
 * const { execute: searchApi, isLoading } = useAsyncDebounce(
 *   async (query) => {
 *     return await api.search(query)
 *   },
 *   500
 * )
 * 
 * // 调用
 * await searchApi('关键词')
 */
export function useAsyncDebounce(asyncFn, delay = 300) {
  const isLoading = ref(false)
  let timeoutId = null
  let currentAbortController = null

  const execute = async (...args) => {
    if (timeoutId) clearTimeout(timeoutId)

    return new Promise((resolve, reject) => {
      timeoutId = setTimeout(async () => {
        try {
          if (currentAbortController) {
            currentAbortController.abort()
          }
          
          currentAbortController = new AbortController()
          isLoading.value = true
          
          const result = await asyncFn(...args, currentAbortController.signal)
          isLoading.value = false
          resolve(result)
        } catch (error) {
          isLoading.value = false
          if (error.name !== 'AbortError') {
            reject(error)
          }
        } finally {
          timeoutId = null
        }
      }, delay)
    })
  }

  const cancel = () => {
    if (timeoutId) {
      clearTimeout(timeoutId)
      timeoutId = null
    }
    if (currentAbortController) {
      currentAbortController.abort()
      currentAbortController = null
    }
    isLoading.value = false
  }

  onUnmounted(() => cancel())

  return { execute, cancel, isLoading }
}

/**
 * 纯函数版本的防抖
 * @param {Function} func - 原始函数
 * @param {number} wait - 等待时间（毫秒）
 * @param {boolean} [immediate=false] - 是否立即执行
 * @returns {Function} 防抖后的函数
 */
export function debounce(func, wait = 300, immediate = false) {
  let timeout
  
  function executedFunction(...args) {
    const later = () => {
      timeout = null
      if (!immediate) func.apply(this, args)
    }
    
    const callNow = immediate && !timeout
    
    clearTimeout(timeout)
    timeout = setTimeout(later, wait)
    
    if (callNow) func.apply(this, args)
  }
  
  executedFunction.cancel = () => {
    clearTimeout(timeout)
    timeout = null
  }
  
  return executedFunction
}

/**
 * 纯函数版本的节流
 * @param {Function} func - 原始函数
 * @param {number} limit - 时间限制（毫秒）
 * @returns {Function} 节流后的函数
 */
export function throttle(func, limit = 100) {
  let inThrottle
  
  function executedFunction(...args) {
    if (!inThrottle) {
      func.apply(this, args)
      inThrottle = true
      setTimeout(() => inThrottle = false, limit)
    }
  }
  
  return executedFunction
}
