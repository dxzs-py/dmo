import { ref, watch, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { deepResearchAPI } from '@/api/research'
import { ResearchTaskStatus } from '@/types'
import { logger } from '@/utils/logger'

/** 轮询上限与退避配置（无状态常量，非实例状态） */
const MAX_POLL_COUNT = 600
const BASE_POLL_INTERVAL = 3000
const MAX_POLL_INTERVAL = 30000
const POLL_BACKOFF_FACTOR = 1.5

/**
 * 深度研究任务轮询 composable（实例安全）
 *
 * 职责：
 *   1. 任务状态轮询（指数退避 + 次数上限，404/终态/超时停止）
 *   2. 研究用时计时（elapsed timer，供进度条计算）
 *   3. 审批全部处理完毕后恢复轮询（watch 待审批数量）
 *   4. onUnmounted 自动清理全部定时器（轮询 timer + elapsed timer），
 *      快速路由切换多次挂载卸载互不污染
 *
 * 实例安全约束：本模块不持有任何模块级可变状态，
 * 全部 timer / 计数 / ref 均为调用实例私有；
 * 外部依赖（任务状态读取、状态写入回调、待审批数量 getter、终态回调）
 * 经参数注入，不反向 import 视图。
 *
 * @param {object} deps
 * @param {import('vue').ComputedRef<object|null>} deps.task - 当前任务（响应式）
 * @param {import('vue').Ref<string|null>} deps.currentTaskId - 当前任务 ID
 * @param {(taskId: string, data: object, options?: object) => void} deps.setTaskStatus - 任务状态写入（researchStore.setTaskStatus）
 * @param {() => number} deps.getPendingApprovalsSize - 待审批数量 getter（approvalStore.pendingApprovals.size）
 * @param {() => void} [deps.onTerminal] - 任务转入终态回调（文档分析清理/加载等视图侧行为）
 */
export function useDeepResearchPolling({
  task,
  currentTaskId,
  setTaskStatus,
  getPendingApprovalsSize,
  onTerminal,
}) {
  /** 已用时长（秒），供进度条计算 */
  const elapsedSeconds = ref(0)

  /** @type {ReturnType<typeof setTimeout>|null} */
  let pollingTimer = null
  /** @type {ReturnType<typeof setInterval>|null} */
  let elapsedTimer = null
  let pollCount = 0
  let currentPollInterval = BASE_POLL_INTERVAL

  const isTerminalStatus = (s) => s === ResearchTaskStatus.COMPLETED || s === ResearchTaskStatus.FAILED

  const pollTaskStatus = async () => {
    if (!task.value || isTerminalStatus(task.value.status)) {
      stopPolling()
      return
    }

    pollCount++
    if (pollCount >= MAX_POLL_COUNT) {
      ElMessage.warning('研究任务轮询超时，请刷新页面查看最新状态')
      stopPolling()
      return
    }

    try {
      const response = await deepResearchAPI.getStatus(task.value.taskId)
      const responseData = response.data.data || response.data
      const prevStatus = task.value.status
      setTaskStatus(currentTaskId.value, responseData)

      if (isTerminalStatus(task.value.status)) {
        stopPolling()
        stopElapsedTimer()
        if (onTerminal) onTerminal()
        return
      }

      if (prevStatus === ResearchTaskStatus.PENDING && task.value.status === ResearchTaskStatus.RUNNING) {
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
        setTaskStatus(currentTaskId.value, { status: ResearchTaskStatus.FAILED, errorMessage: '研究任务不存在或已被删除' })
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

  const stopPolling = () => {
    if (pollingTimer) {
      clearTimeout(pollingTimer)
      pollingTimer = null
    }
    currentPollInterval = BASE_POLL_INTERVAL
  }

  /** 以当前轮询间隔调度下一轮轮询（SSE 断开/结束后的回退入口） */
  const startPolling = () => {
    pollingTimer = setTimeout(pollTaskStatus, currentPollInterval)
  }

  const startElapsedTimer = () => {
    stopElapsedTimer()
    elapsedTimer = setInterval(() => {
      elapsedSeconds.value++
    }, 1000)
  }

  const stopElapsedTimer = () => {
    if (elapsedTimer) {
      clearInterval(elapsedTimer)
      elapsedTimer = null
    }
  }

  /** 重置轮询计数、间隔与用时计时（启动新任务前调用） */
  const resetPollingState = () => {
    pollCount = 0
    currentPollInterval = BASE_POLL_INTERVAL
    elapsedSeconds.value = 0
  }

  // 审批清除后恢复轮询（所有待审批项处理完后，后台可能已完成但前端未刷新）
  let prevPendingSize = 0
  watch(
    getPendingApprovalsSize,
    (newSize) => {
      if (prevPendingSize > 0 && newSize === 0 && task.value?.status === ResearchTaskStatus.RUNNING) {
        logger.info('[DeepResearch] 审批已全部处理，恢复轮询')
        pollTaskStatus()
      }
      prevPendingSize = newSize
    }
  )

  // 实例卸载自动清理全部定时器（幂等；视图 onDeactivated/onUnmounted 显式调用同样安全）
  onUnmounted(() => {
    stopPolling()
    stopElapsedTimer()
  })

  return {
    elapsedSeconds,
    isTerminalStatus,
    pollTaskStatus,
    startPolling,
    stopPolling,
    startElapsedTimer,
    stopElapsedTimer,
    resetPollingState,
  }
}
