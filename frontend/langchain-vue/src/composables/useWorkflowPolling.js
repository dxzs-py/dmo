import { onUnmounted } from 'vue'
import { workflowAPI } from '@/api/workflow'
import { logger } from '@/utils/logger'
import { LearningStep } from '@/types'

/** 轮询退避常量（不可变配置，非实例可变状态，模块级共享安全） */
const BASE_POLL_INTERVAL = 3000
const MAX_POLL_INTERVAL = 30000
const POLL_BACKOFF_FACTOR = 1.5

/**
 * 学习工作流状态轮询 composable
 *
 * 职责（自 WorkflowView 轮询域迁移，行为逐字保持）：
 *   - pollExecutionStatus：定时拉取工作流状态并合并到 execution；
 *     步骤变化时重置退避、无变化/失败时按 1.5 倍退避（上限 30s）
 *   - schedulePolling：单次调度下一轮轮询（SSE 断开后回退轮询场景，
 *     由 useWorkflowExecution 调用，等价原 connectSSE 内联的 setTimeout）
 *   - stopPolling：终止轮询并重置退避间隔
 *
 * 实例安全：pollingTimer / currentPollInterval 均为调用闭包内私有，
 * 每次调用返回全新独立状态，无模块级可变全局；onUnmounted 自动清理定时器。
 *
 * @param {object} deps
 * @param {import('vue').Ref<object|null>} deps.execution - 工作流执行状态（读写合并）
 * @param {object} deps.answersForm - 答题表单（quiz 首次到达时初始化各题空答案）
 * @param {() => void} deps.onTerminal - 终态回调（END/COMPLETED 时加载关键文件，
 *   WAITING_FOR_ANSWERS 不触发——与原 autoLoadKeyFile 调用条件一致）
 * @returns {{
 *   pollExecutionStatus: () => Promise<void>,
 *   stopPolling: () => void,
 *   schedulePolling: () => void,
 * }}
 */
export function useWorkflowPolling({ execution, answersForm, onTerminal }) {
  /** @type {ReturnType<typeof setTimeout> | null} */
  let pollingTimer = null
  let currentPollInterval = BASE_POLL_INTERVAL

  const pollExecutionStatus = async () => {
    if (!execution.value) {
      stopPolling()
      return
    }

    const currentStep = execution.value.currentStep

    if (currentStep === LearningStep.WAITING_FOR_ANSWERS || currentStep === LearningStep.END || currentStep === LearningStep.COMPLETED) {
      stopPolling()
      if (currentStep !== LearningStep.WAITING_FOR_ANSWERS) {
        onTerminal()
      }
      return
    }

    try {
      const response = await workflowAPI.getState(execution.value.threadId)
      const data = response.data
      execution.value = { ...execution.value, ...(data.data || data) }

      const responseData = data.data || data
      if (responseData.quiz && !Object.keys(answersForm).length) {
        responseData.quiz.questions.forEach(q => {
          answersForm[q.id] = ''
        })
      }

      if (currentStep !== execution.value.currentStep) {
        currentPollInterval = BASE_POLL_INTERVAL
      } else {
        currentPollInterval = Math.min(
          Math.floor(currentPollInterval * POLL_BACKOFF_FACTOR),
          MAX_POLL_INTERVAL
        )
      }
      pollingTimer = setTimeout(pollExecutionStatus, currentPollInterval)
    } catch (error) {
      logger.error('获取工作流状态失败:', error)
      currentPollInterval = Math.min(
        Math.floor(currentPollInterval * POLL_BACKOFF_FACTOR),
        MAX_POLL_INTERVAL
      )
      pollingTimer = setTimeout(pollExecutionStatus, currentPollInterval)
    }
  }

  const stopPolling = () => {
    if (pollingTimer) {
      clearTimeout(pollingTimer)
      pollingTimer = null
    }
    currentPollInterval = BASE_POLL_INTERVAL
  }

  /**
   * 调度下一轮轮询（单次 setTimeout，之后由 pollExecutionStatus 自续）
   * 等价原 connectSSE 内联的 `pollingTimer = setTimeout(pollExecutionStatus, currentPollInterval)`
   */
  const schedulePolling = () => {
    pollingTimer = setTimeout(pollExecutionStatus, currentPollInterval)
  }

  // 实例卸载自动清理定时器（keep-alive 失活由视图 onDeactivated 显式调用 stopPolling）
  onUnmounted(stopPolling)

  return { pollExecutionStatus, stopPolling, schedulePolling }
}
