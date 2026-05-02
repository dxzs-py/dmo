import { ref, onUnmounted } from 'vue'

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
