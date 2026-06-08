import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

const MIN_DISPLAY_MS = 300

export const useLoadingStore = defineStore('loading', () => {
  const activeRequests = ref(0)
  const isLoading = computed(() => startTime.value > 0)
  const startTime = ref(0)

  function start() {
    if (activeRequests.value === 0) {
      startTime.value = Date.now()
    }
    activeRequests.value++
  }

  function stop() {
    if (activeRequests.value <= 0) return
    activeRequests.value--
    if (activeRequests.value === 0 && startTime.value > 0) {
      const elapsed = Date.now() - startTime.value
      if (elapsed < MIN_DISPLAY_MS) {
        const remain = MIN_DISPLAY_MS - elapsed
        const capturedStart = startTime.value
        setTimeout(() => {
          if (startTime.value === capturedStart) {
            startTime.value = 0
          }
        }, remain)
      } else {
        startTime.value = 0
      }
    }
  }

  function reset() {
    activeRequests.value = 0
    startTime.value = 0
  }

  return { activeRequests, isLoading, start, stop, reset }
})
