import { ref, getCurrentScope, onScopeDispose } from 'vue'
import { usePollingBackoff } from './usePollingBackoff'
import { logger } from '@/utils/logger'

/**
 * 通用轮询骨架 composable（useDeepResearchPolling / useWorkflowPolling 共用）
 *
 * 封装轮询骨架的通用部分，域专属逻辑全部落在 tick/shouldStop 回调内：
 *   - 定时器自续：每轮 tick 结束后按当前退避间隔调度下一轮（单链不变量：
 *     每次调度前先清除残留句柄，任意时刻至多一个待触发定时器）
 *   - usePollingBackoff 集成：错误/退避节奏由 backoff 实例决定——tick 内经
 *     stepBackoff/resetBackoff 调整节奏，骨架只按 currentDelay 调度
 *   - 错误自续：tick 抛错（域内未捕获的异常）经一步退避后自续，不中断轮询
 *   - stopPolling：终止轮询并重置退避（幂等；对进行中的 tick 同样生效——
 *     自续前复查停止态，stopPolling 后不再触发）
 *   - 组件卸载自动清理（onScopeDispose，组件 unmount 即触发，覆盖
 *     onUnmounted 语义；与 useApiTask 的清理模式一致）
 *
 * @param {object} options
 * @param {() => Promise<void>|void} options.tick - 每轮域逻辑；需要终止时调用
 *   stopPolling()，需要调整节奏时调用 stepBackoff()/resetBackoff()，返回后骨架自续
 * @param {() => boolean} [options.shouldStop] - 每轮 tick 前的终止判定（无副作用守卫），
 *   返回 true 则停止轮询且本轮 tick 不执行
 * @param {number} [options.interval=3000] - 基础轮询间隔 ms（usePollingBackoff baseDelay）
 * @param {{ maxDelay?: number, multiplier?: number }} [options.backoff] - 退避参数
 *   （usePollingBackoff 的 maxDelay/multiplier）
 * @returns {{
 *   startPolling: () => void,
 *   stopPolling: () => void,
 *   pollNow: () => Promise<void>,
 *   pollingTimer: import('vue').Ref<ReturnType<typeof setTimeout>|null>,
 *   currentDelay: import('vue').Ref<number>,
 *   stepBackoff: () => void,
 *   resetBackoff: () => void,
 * }}
 */
export function usePollingLoop({ tick, shouldStop, interval = 3000, backoff: backoffOptions = {} } = {}) {
  const { currentDelay, backoff: stepBackoff, reset: resetBackoff } = usePollingBackoff({
    baseDelay: interval,
    ...backoffOptions,
  })

  /** 待触发的轮询定时器句柄 */
  const pollingTimer = ref(null)
  /** 停止态标志：stopPolling 置位，防止进行中的 tick 完成后继续自续 */
  let isStopped = true

  /** 以当前退避间隔调度下一轮（先清残留句柄保证单链；已停止则不调度） */
  const scheduleNext = () => {
    if (isStopped) return
    if (pollingTimer.value) clearTimeout(pollingTimer.value)
    pollingTimer.value = setTimeout(runTick, currentDelay.value)
  }

  /**
   * 单轮执行：shouldStop 判定 → tick → 自续。
   * tick 抛错经一步退避后自续，不中断轮询；
   * tick 内调用 stopPolling（终态/404/次数上限等）后不再自续。
   */
  const runTick = async () => {
    pollingTimer.value = null
    if (shouldStop && shouldStop()) {
      stopPolling()
      return
    }
    try {
      await tick()
    } catch (error) {
      logger.error('[usePollingLoop] tick 执行异常，退避后自动续轮:', error)
      stepBackoff()
    }
    if (isStopped) return
    scheduleNext()
  }

  /** 启动轮询：以当前间隔调度下一轮（SSE 断开/结束后的回退入口） */
  const startPolling = () => {
    isStopped = false
    scheduleNext()
  }

  /**
   * 立即执行一轮完整轮询（tick + 自续），不等当前间隔
   * （审批恢复 watch 等需要立刻拉取一次的场景）；
   * 同时取消已排程的一轮，避免双链（立即执行的一轮已覆盖该次拉取）。
   */
  const pollNow = () => {
    isStopped = false
    if (pollingTimer.value) {
      clearTimeout(pollingTimer.value)
      pollingTimer.value = null
    }
    return runTick()
  }

  /** 终止轮询：清除待触发定时器并重置退避间隔（幂等） */
  const stopPolling = () => {
    isStopped = true
    if (pollingTimer.value) {
      clearTimeout(pollingTimer.value)
      pollingTimer.value = null
    }
    resetBackoff()
  }

  // 实例卸载自动清理（幂等；组件 setup 的 effect scope 在 unmount 时 dispose）
  if (getCurrentScope()) {
    onScopeDispose(stopPolling)
  }

  return { startPolling, stopPolling, pollNow, pollingTimer, currentDelay, stepBackoff, resetBackoff }
}
