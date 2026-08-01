import { ref } from 'vue'
import { deepResearchAPI } from '@/api'
import { ElMessage } from 'element-plus'
import { logger } from '@/utils/logger'

/**
 * 深度研究任务轮询 composable
 *
 * 在 SSE 不可用或流结束后作为兜底机制，按指数退避轮询任务状态。
 * 轮询参数：基础间隔 3s，最大 30s，退避因子 1.5，最大次数 600（约 30 分钟）。
 *
 * @param {Object} deps
 * @param {import('vue').Ref<Object|null>} deps.task - 当前任务 ref
 * @param {import('vue').Ref<Object|null>} deps.fileBrowserRef - FileBrowser 组件 ref
 * @param {() => void} deps.stopElapsedTimer - 停止计时器
 * @param {() => void} deps.checkDocAnalysisFile - 重置文档分析状态
 * @param {() => Promise<void>} deps.autoLoadDocAnalysis - 自动加载文档分析
 * @returns {{
 *   pollTaskStatus: () => Promise<void>,
 *   stopPolling: () => void,
 *   resetPolling: () => void,
 *   pollCount: import('vue').Ref<number>,
 * }}
 */
export function useResearchPolling({ task, fileBrowserRef, stopElapsedTimer, checkDocAnalysisFile, autoLoadDocAnalysis }) {
  const MAX_POLL_COUNT = 600
  const BASE_POLL_INTERVAL = 3000
  const MAX_POLL_INTERVAL = 30000
  const POLL_BACKOFF_FACTOR = 1.5

  /** @type {number | null} */
  let pollingTimer = null
  const pollCount = ref(0)
  let currentPollInterval = BASE_POLL_INTERVAL

  /**
   * 重置轮询状态（启动新任务前调用）
   */
  const resetPolling = () => {
    pollCount.value = 0
    currentPollInterval = BASE_POLL_INTERVAL
  }

  const stopPolling = () => {
    if (pollingTimer) {
      clearTimeout(pollingTimer)
      pollingTimer = null
    }
    currentPollInterval = BASE_POLL_INTERVAL
  }

  /**
   * 启动一次轮询（递归调度自身）
   * 任务完成/失败/不存在时自动停止；其余情况按指数退避继续轮询
   */
  const pollTaskStatus = async () => {
    if (!task.value || task.value.status === 'completed' || task.value.status === 'failed') {
      stopPolling()
      if (fileBrowserRef.value) {
        fileBrowserRef.value.loadFiles()
      }
      return
    }

    pollCount.value++
    if (pollCount.value >= MAX_POLL_COUNT) {
      ElMessage.warning('研究任务轮询超时，请刷新页面查看最新状态')
      stopPolling()
      return
    }

    try {
      const response = await deepResearchAPI.getStatus(task.value.task_id)
      const responseData = response.data.data || response.data
      const prevStatus = task.value.status
      task.value = { ...task.value, ...responseData }

      if (task.value.status === 'completed' || task.value.status === 'failed') {
        stopPolling()
        stopElapsedTimer()
        checkDocAnalysisFile()
        if (fileBrowserRef.value) {
          fileBrowserRef.value.loadFiles()
        }
        autoLoadDocAnalysis()
        return
      }

      if (prevStatus === 'pending' && task.value.status === 'running') {
        currentPollInterval = BASE_POLL_INTERVAL
      } else {
        currentPollInterval = Math.min(
          Math.floor(currentPollInterval * POLL_BACKOFF_FACTOR),
          MAX_POLL_INTERVAL
        )
      }

      pollingTimer = setTimeout(pollTaskStatus, currentPollInterval)
    } catch (error) {
      logger.error('获取任务状态失败:', error)
      if (error?.response?.status === 404) {
        task.value.status = 'failed'
        task.value.error_message = '研究任务不存在或已被删除'
        stopPolling()
        return
      }
      currentPollInterval = Math.min(
        Math.floor(currentPollInterval * POLL_BACKOFF_FACTOR),
        MAX_POLL_INTERVAL
      )
      pollingTimer = setTimeout(pollTaskStatus, currentPollInterval)
    }
  }

  /**
   * 启动轮询（按当前轮询间隔调度一次 pollTaskStatus）
   * 供 SSE done/timeout/fallback 兜底调用
   */
  const startPolling = () => {
    pollingTimer = setTimeout(pollTaskStatus, currentPollInterval)
  }

  return {
    pollTaskStatus,
    stopPolling,
    resetPolling,
    startPolling,
    pollCount,
  }
}
