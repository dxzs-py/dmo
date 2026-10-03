import { ref, watch, getCurrentScope, onScopeDispose } from 'vue'
import { ElMessage } from 'element-plus'
import { deepResearchAPI } from '@/api/research'
import { ResearchTaskStatus } from '@/types'
import { logger } from '@/utils/logger'
import { usePollingLoop } from './usePollingLoop'

/** 轮询次数上限（无状态常量，非实例状态） */
const MAX_POLL_COUNT = 600
/** 轮询参数：基础间隔 3s / 退避上限 30s / 1.5 倍退避 */
const POLLING_OPTIONS = { interval: 3000, backoff: { maxDelay: 30000, multiplier: 1.5 } }

/**
 * 深度研究任务轮询 composable（实例安全，薄壳）
 *
 * 轮询骨架（定时器自续 / 退避 / 错误自续 / 卸载清理）由 usePollingLoop 统一实现，
 * 本模块仅保留域专属逻辑：
 *   1. tick（pollTaskStatus）：次数上限（MAX_POLL_COUNT，超时告警）、状态拉取与
 *      写入、终态处理（elapsed 停表 + onTerminal）、节奏微调（PENDING→RUNNING
 *      重置退避，其余按 1.5 倍退避）、404 归档为 FAILED 并终止
 *   2. shouldStop：无任务或已终态时停止（等价原 pollTaskStatus 顶部前置检查）
 *   3. 研究用时计时（elapsed timer，供进度条计算）
 *   4. 审批全部处理完毕后恢复轮询（watch 待审批数量 → pollNow 立即拉取一轮）
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

  /** @type {ReturnType<typeof setInterval>|null} */
  let elapsedTimer = null
  let pollCount = 0

  const isTerminalStatus = (s) => s === ResearchTaskStatus.COMPLETED || s === ResearchTaskStatus.FAILED

  /**
   * 单轮域逻辑：次数上限 / 状态拉取写入 / 终态处理 / 节奏微调。
   * 终止经 stopPolling()，节奏经 resetPollInterval()/backoffPollInterval() 调整，
   * 下一轮调度由 usePollingLoop 统一完成。
   */
  const pollTaskStatus = async () => {
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
        resetPollInterval()
      } else {
        backoffPollInterval()
      }
    } catch (error) {
      logger.error('获取任务状态失败:', error)
      if (error?.response?.status === 404) {
        setTaskStatus(currentTaskId.value, { status: ResearchTaskStatus.FAILED, errorMessage: '研究任务不存在或已被删除' })
        stopPolling()
        return
      }
      backoffPollInterval()
    }
  }

  // 轮询骨架：定时器自续 / 退避 / tick 抛错自续 / 卸载清理统一托管
  const {
    startPolling,
    stopPolling,
    pollNow,
    currentDelay: currentPollInterval,
    stepBackoff: backoffPollInterval,
    resetBackoff: resetPollInterval,
  } = usePollingLoop({
    tick: pollTaskStatus,
    shouldStop: () => !task.value || isTerminalStatus(task.value.status),
    ...POLLING_OPTIONS,
  })

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
    resetPollInterval()
    elapsedSeconds.value = 0
  }

  // 审批清除后恢复轮询（所有待审批项处理完后，后台可能已完成但前端未刷新）
  let prevPendingSize = 0
  watch(
    getPendingApprovalsSize,
    (newSize) => {
      if (prevPendingSize > 0 && newSize === 0 && task.value?.status === ResearchTaskStatus.RUNNING) {
        logger.info('[DeepResearch] 审批已全部处理，恢复轮询')
        pollNow()
      }
      prevPendingSize = newSize
    }
  )

  // elapsed 定时器随实例卸载清理（轮询定时器由 usePollingLoop 内部清理；
  // 视图 onDeactivated/onUnmounted 显式调用 stopPolling/stopElapsedTimer 同样安全）
  if (getCurrentScope()) {
    onScopeDispose(stopElapsedTimer)
  }

  return {
    elapsedSeconds,
    isTerminalStatus,
    // 对外暴露完整轮询周期（含自续）；裸 tick 仅由 usePollingLoop 调度
    pollTaskStatus: pollNow,
    startPolling,
    stopPolling,
    startElapsedTimer,
    stopElapsedTimer,
    resetPollingState,
  }
}
