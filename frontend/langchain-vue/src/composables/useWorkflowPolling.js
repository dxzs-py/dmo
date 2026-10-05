import { workflowAPI } from '@/api/workflow'
import { logger } from '@/utils/logger'
import { LearningStep } from '@/types'
import { usePollingLoop } from './usePollingLoop'

/** 轮询参数：基础间隔 3s / 退避上限 30s / 1.5 倍退避（不可变配置，模块级共享安全） */
const POLLING_OPTIONS = { interval: 3000, backoff: { maxDelay: 30000, multiplier: 1.5 } }

/**
 * 学习工作流状态轮询 composable（薄壳：轮询骨架由 usePollingLoop 统一实现）
 *
 * 域专属逻辑（自 WorkflowView 轮询域迁移，行为逐字保持）：
 *   - tick（pollExecutionStatus）：终态步骤判定（END/COMPLETED 停止并触发
 *     onTerminal，WAITING_FOR_ANSWERS 仅停止）、拉取工作流状态合并到 execution、
 *     quiz 首次到达时初始化 answersForm 各题空答案；步骤变化重置退避、
 *     无变化按 1.5 倍退避（上限 30s）
 *   - shouldStop：无 execution 时停止（等价原 pollExecutionStatus 顶部前置检查）
 *   - schedulePolling（usePollingLoop.startPolling）：单次调度下一轮轮询
 *     （SSE 断开后回退轮询场景，由 useWorkflowExecution 调用，
 *     等价原 connectSSE 内联的 setTimeout）
 *   - stopPolling：终止轮询并重置退避间隔
 *
 * 实例安全：pollingTimer / 退避间隔均由 usePollingLoop 实例私有，
 * 每次调用返回全新独立状态，无模块级可变全局；
 * 组件卸载时经 onScopeDispose 自动清理定时器（keep-alive 失活由视图
 * onDeactivated 显式调用 stopPolling）。
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
  /**
   * 单轮域逻辑：终态判定 / 状态合并 / quiz 初始化 / 节奏微调。
   * 终止经 stopPolling()，节奏经 resetPollInterval()/backoffPollInterval() 调整，
   * 下一轮调度由 usePollingLoop 统一完成。
   */
  const pollExecutionStatus = async () => {
    const currentStep = execution.value.currentStep

    // 终态判定：必须包含 FEEDBACK_COMPLETED —— 后端 feedback_node 成功时写入的
    // current_step 就是 'feedback_completed'（study_flow 图的终态节点是 END，
    // 但 current_step 不会取字面量 'end'/'completed'）。此前此处漏判，导致：
    //   1) 轮询不停止，持续无谓请求；
    //   2) SSE 不可用时（轮询即降级路径）onTerminal 不触发，收尾逻辑（如答案文件
    //      自动加载）不执行。
    // 与 WorkflowView.canContinue / WorkflowView 步骤展示 / FileBrowser
    // TERMINAL_TASK_STATUSES 的口径保持一致（那三处都已包含 feedback_completed）。
    if (
      currentStep === LearningStep.WAITING_FOR_ANSWERS ||
      currentStep === LearningStep.FEEDBACK_COMPLETED ||
      currentStep === LearningStep.END ||
      currentStep === LearningStep.COMPLETED
    ) {
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
        resetPollInterval()
      } else {
        backoffPollInterval()
      }
    } catch (error) {
      logger.error('获取工作流状态失败:', error)
      backoffPollInterval()
    }
  }

  // 轮询骨架：定时器自续 / 退避 / tick 抛错自续 / 卸载清理统一托管
  const {
    startPolling: schedulePolling,
    stopPolling,
    pollNow,
    stepBackoff: backoffPollInterval,
    resetBackoff: resetPollInterval,
  } = usePollingLoop({
    tick: pollExecutionStatus,
    shouldStop: () => !execution.value,
    ...POLLING_OPTIONS,
  })

  return {
    // 对外暴露完整轮询周期（含自续）；裸 tick 仅由 usePollingLoop 调度
    pollExecutionStatus: pollNow,
    stopPolling,
    schedulePolling,
  }
}
