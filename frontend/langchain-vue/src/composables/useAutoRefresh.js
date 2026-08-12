import { ref, onUnmounted } from 'vue'
import { logger } from '@/utils/logger'

/**
 * 通用自动刷新 / 轮询加载 composable
 *
 * 用途：任何需要在"数据尚未就绪时自动重试，直到满足条件或达到上限"的模块
 * （如 FileBrowser 文件列表、任务列表、研究报告等）统一复用本逻辑，
 * 避免各模块各自维护 setTimeout 轮询导致逻辑漂移。
 *
 * 停止条件（满足其一即停止轮询）：
 *  1. shouldStop(data) 返回 true（数据就绪）
 *  2. 尝试次数达到 maxAttempts（上限保护）
 *  3. 手动调用 stop()
 *
 * @param {Function} fetcher - 异步数据获取函数，resolve 后的返回值传给 shouldStop / onData
 * @param {Object} [options]
 * @param {number} [options.interval=3000] - 轮询间隔（ms）
 * @param {number} [options.maxAttempts=30] - 最大轮询次数，0 表示无限
 * @param {Function} [options.shouldStop] - (data) => boolean，满足后停止轮询
 * @param {Function} [options.onData] - (data) => void，每次成功获取后回调
 * @param {boolean} [options.immediate=true] - 是否立即执行第一次获取
 * @param {string} [options.source='autoRefresh'] - 日志标识
 * @returns {{
 *   data: import('vue').Ref,
 *   loading: import('vue').Ref<boolean>,
 *   error: import('vue').Ref<Error|null>,
 *   attempts: import('vue').Ref<number>,
 *   isActive: import('vue').Ref<boolean>,
 *   refresh: () => Promise<void>,
 *   start: () => void,
 *   stop: () => void,
 * }}
 */
export function useAutoRefresh(fetcher, options = {}) {
  const {
    interval = 3000,
    maxAttempts = 30,
    shouldStop = null,
    onData = null,
    immediate = true,
    source = 'autoRefresh',
  } = options

  const data = ref(null)
  const loading = ref(false)
  const error = ref(null)
  const attempts = ref(0)
  const isActive = ref(false)

  /** @type {ReturnType<typeof setTimeout> | null} */
  let timer = null
  /** @type {boolean} */
  let disposed = false

  const clearTimer = () => {
    if (timer) {
      clearTimeout(timer)
      timer = null
    }
  }

  /**
   * 执行单次数据获取（轮询或手动刷新共用）
   * @param {boolean} manual - 手动刷新（不计入轮询次数、不触发停止条件）
   * @returns {Promise<boolean>} 是否应继续轮询
   */
  const fetchOnce = async (manual = false) => {
    if (disposed) return false
    loading.value = true
    if (!manual) attempts.value += 1
    try {
      const result = await fetcher()
      if (disposed) return false
      data.value = result
      error.value = null
      if (typeof onData === 'function') onData(result)
      if (!manual && typeof shouldStop === 'function' && shouldStop(result)) {
        stop()
        return false
      }
    } catch (e) {
      if (disposed) return false
      logger.warn(`[${source}] 自动刷新失败（第 ${attempts.value} 次）`, e)
      error.value = e
    } finally {
      if (!disposed) loading.value = false
    }
    if (!manual && maxAttempts > 0 && attempts.value >= maxAttempts) {
      logger.warn(`[${source}] 达到最大尝试次数 ${maxAttempts}，停止自动刷新`)
      stop()
      return false
    }
    return isActive.value
  }

  const tick = async () => {
    if (!isActive.value || disposed) return
    const shouldContinue = await fetchOnce(false)
    if (shouldContinue && !disposed) {
      timer = setTimeout(tick, interval)
    }
  }

  /** 启动自动刷新（幂等） */
  const start = () => {
    if (isActive.value || disposed) return
    isActive.value = true
    tick()
  }

  /** 停止自动刷新（幂等） */
  const stop = () => {
    isActive.value = false
    clearTimer()
  }

  /** 手动刷新一次（不参与轮询计数与停止判断） */
  const refresh = async () => {
    await fetchOnce(true)
  }

  onUnmounted(() => {
    disposed = true
    stop()
  })

  if (immediate) start()

  return { data, loading, error, attempts, isActive, refresh, start, stop }
}
