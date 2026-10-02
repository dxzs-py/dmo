import { ref } from 'vue'

/**
 * 轮询指数退避骨架（useDeepResearchPolling / useWorkflowPolling 共用）
 *
 * 封装「当前间隔自基础值起、每轮按倍数放大、上限封顶、可重置回基础值」的
 * 退避状态机；调用方只消费 currentDelay / backoff / reset，轮询语义不变。
 *
 * @param {object} [options]
 * @param {number} [options.baseDelay=3000] - 基础轮询间隔（ms）
 * @param {number} [options.maxDelay=30000] - 轮询间隔上限（ms）
 * @param {number} [options.multiplier=1.5] - 退避放大倍数
 * @returns {{
 *   currentDelay: import('vue').Ref<number>,
 *   backoff: () => void,
 *   reset: () => void,
 * }}
 */
export function usePollingBackoff({ baseDelay = 3000, maxDelay = 30000, multiplier = 1.5 } = {}) {
  /** 当前轮询间隔（ms） */
  const currentDelay = ref(baseDelay)

  /** 退避一步：floor(当前间隔 × 倍数)，不超过上限 */
  const backoff = () => {
    currentDelay.value = Math.min(Math.floor(currentDelay.value * multiplier), maxDelay)
  }

  /** 重置回基础间隔（步骤变化 / 停止轮询 / 重置状态时调用） */
  const reset = () => {
    currentDelay.value = baseDelay
  }

  return { currentDelay, backoff, reset }
}
