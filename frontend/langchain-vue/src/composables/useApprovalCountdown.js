import { computed, ref, toValue, watch } from 'vue'
import { useIntervalFn } from '@vueuse/core'

/** 预警阈值：剩余时间低于该值进入视觉预警（红色，Element Plus 语义色） */
const EXPIRY_WARN_THRESHOLD_MS = 60 * 1000

/** 倒计时刷新间隔（毫秒） */
const TICK_INTERVAL_MS = 1000

/**
 * 审批倒计时 composable（F3/UX-01：审批面板剩余时间展示）
 *
 * 纯展示语义：到期仅置 isExpired 供模板隐藏倒计时，不触发任何超时恢复
 * 副作用（既有超时恢复链路 approval/eventHandlers.js 不受影响）。
 * 定时器经 @vueuse/core useIntervalFn 托管：非活跃/已到期自动暂停，
 * 组件卸载（scope dispose）自动清理，无泄漏。
 *
 * @param {import('vue').MaybeRefOrGetter<number>} expirySource - 过期时刻（epoch ms）；
 *   非有限正数视为无效，倒计时不激活
 * @param {import('vue').MaybeRefOrGetter<boolean>} activeSource - 是否处于应展示倒计时的活跃审批态
 * @param {object} [options]
 * @param {number} [options.warnThresholdMs=60000] - 预警阈值（毫秒）
 * @returns {{
 *   remainingMs: import('vue').ComputedRef<number>,
 *   remainingText: import('vue').ComputedRef<string>,
 *   isExpiringSoon: import('vue').ComputedRef<boolean>,
 *   isExpired: import('vue').ComputedRef<boolean>,
 * }}
 */
export function useApprovalCountdown(expirySource, activeSource, options = {}) {
  const warnThresholdMs = options.warnThresholdMs ?? EXPIRY_WARN_THRESHOLD_MS

  /** 当前时刻快照（定时器驱动刷新；组件内响应式，避免渲染期裸读 Date.now） */
  const nowMs = ref(Date.now())

  const { pause, resume } = useIntervalFn(() => {
    nowMs.value = Date.now()
  }, TICK_INTERVAL_MS, { immediate: false })

  const isActive = computed(() => toValue(activeSource) === true)

  const hasExpiry = computed(() => {
    const ms = toValue(expirySource)
    return typeof ms === 'number' && Number.isFinite(ms) && ms > 0
  })

  /** 剩余毫秒（非活跃或无效过期时刻恒为 0，模板不渲染） */
  const remainingMs = computed(() => {
    if (!isActive.value || !hasExpiry.value) return 0
    return Math.max(0, Math.floor(toValue(expirySource) - nowMs.value))
  })

  const isExpired = computed(() =>
    isActive.value && hasExpiry.value && toValue(expirySource) - nowMs.value <= 0
  )

  const isExpiringSoon = computed(() =>
    !isExpired.value && remainingMs.value > 0 && remainingMs.value <= warnThresholdMs
  )

  /** mm:ss 形态剩余时间（分钟数不封顶，如 "04:30"） */
  const remainingText = computed(() => {
    const totalSeconds = Math.floor(remainingMs.value / 1000)
    const minutes = Math.floor(totalSeconds / 60)
    const seconds = totalSeconds % 60
    return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
  })

  // 活跃且未到期才持有定时器：激活瞬间刷新时刻快照（防长暂停后 nowMs 陈旧），
  // 到期/失活自动暂停，卸载由 useIntervalFn 的 scope dispose 兜底
  watch([isActive, isExpired], ([active, expired]) => {
    if (active && !expired) {
      nowMs.value = Date.now()
      resume()
    } else {
      pause()
    }
  }, { immediate: true })

  return { remainingMs, remainingText, isExpiringSoon, isExpired }
}
