import { describe, it, expect, vi, afterEach } from 'vitest'
import { ref, computed, nextTick } from 'vue'
import { effectScope } from 'vue'
import { useApprovalCountdown } from '../useApprovalCountdown'

/**
 * F3/UX-01 审批倒计时 composable 单测
 *
 * 覆盖：mm:ss 渲染、非活跃不激活、到期判定与隐藏、<60s 预警阈值、
 * 到期后定时器清理（防泄漏）、卸载清理（scope dispose）、过期时刻动态更新。
 */

describe('useApprovalCountdown（F3/UX-01 审批剩余时间倒计时）', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('mm:ss 渲染：剩余 4 分 30 秒显示 "04:30"', () => {
    vi.useFakeTimers()
    vi.setSystemTime(1_000_000)
    const expiry = ref(1_000_000 + 270_000)
    const { remainingText } = useApprovalCountdown(expiry, ref(true))
    expect(remainingText.value).toBe('04:30')
  })

  it('非活跃（终态/等待同批）时剩余恒为 0 且不判过期', () => {
    vi.useFakeTimers()
    vi.setSystemTime(1_000_000)
    const expiry = ref(1_000_000 + 270_000)
    const { remainingMs, remainingText, isExpired, isExpiringSoon } = useApprovalCountdown(expiry, ref(false))
    expect(remainingMs.value).toBe(0)
    expect(remainingText.value).toBe('00:00')
    expect(isExpired.value).toBe(false)
    expect(isExpiringSoon.value).toBe(false)
    // 非活跃不持有定时器
    expect(vi.getTimerCount()).toBe(0)
  })

  it('无效过期时刻（0/NaN）视为未激活', () => {
    vi.useFakeTimers()
    vi.setSystemTime(1_000_000)
    const { remainingMs, isExpired } = useApprovalCountdown(ref(0), ref(true))
    expect(remainingMs.value).toBe(0)
    expect(isExpired.value).toBe(false)
    const { isExpired: expiredNaN } = useApprovalCountdown(ref(Number.NaN), ref(true))
    expect(expiredNaN.value).toBe(false)
  })

  it('已过过期时刻：isExpired 置真、剩余钳 0（模板据此隐藏倒计时）', () => {
    vi.useFakeTimers()
    vi.setSystemTime(1_000_000)
    const expiry = ref(1_000_000 - 1000)
    const { remainingMs, remainingText, isExpired, isExpiringSoon } = useApprovalCountdown(expiry, ref(true))
    expect(isExpired.value).toBe(true)
    expect(remainingMs.value).toBe(0)
    expect(remainingText.value).toBe('00:00')
    expect(isExpiringSoon.value).toBe(false)
  })

  it('预警阈值：剩余 ≤60s 时 isExpiringSoon 为真，>60s 为假', () => {
    vi.useFakeTimers()
    vi.setSystemTime(1_000_000)
    const expiring = useApprovalCountdown(ref(1_000_000 + 30_000), ref(true))
    expect(expiring.isExpiringSoon.value).toBe(true)

    const safe = useApprovalCountdown(ref(1_000_000 + 61_000), ref(true))
    expect(safe.isExpiringSoon.value).toBe(false)

    // 边界：恰好 60s 触发预警
    const boundary = useApprovalCountdown(ref(1_000_000 + 60_000), ref(true))
    expect(boundary.isExpiringSoon.value).toBe(true)
  })

  it('定时器驱动刷新且到期后自动清理（防泄漏）', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(0)
    const expiry = ref(2000)
    const { remainingText, isExpired } = useApprovalCountdown(expiry, ref(true))

    await vi.advanceTimersByTimeAsync(1000)
    await nextTick()
    expect(remainingText.value).toBe('00:01')
    expect(vi.getTimerCount()).toBe(1)

    // 越过过期时刻：isExpired 置真，定时器被 watch 暂停清理
    await vi.advanceTimersByTimeAsync(1500)
    await nextTick()
    expect(isExpired.value).toBe(true)
    expect(remainingText.value).toBe('00:00')
    expect(vi.getTimerCount()).toBe(0)
  })

  it('scope dispose（组件卸载）自动清理定时器', () => {
    vi.useFakeTimers()
    vi.setSystemTime(0)
    const scope = effectScope()
    scope.run(() => useApprovalCountdown(ref(60_000), ref(true)))
    expect(vi.getTimerCount()).toBe(1)
    scope.stop()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('过期时刻动态更新（store 条目后到）：剩余时间随之刷新', () => {
    vi.useFakeTimers()
    vi.setSystemTime(1_000_000)
    const expiry = ref(0)
    const { remainingText } = useApprovalCountdown(expiry, ref(true))
    // 条目未到：不激活
    expect(remainingText.value).toBe('00:00')
    // 条目到达（真实超时点写入 store）
    expiry.value = 1_000_000 + 125_000
    expect(remainingText.value).toBe('02:05')
  })

  it('activeSource 支持 getter 形态（与组件 computed 消费形态一致）', () => {
    vi.useFakeTimers()
    vi.setSystemTime(1_000_000)
    const active = computed(() => true)
    const { remainingText } = useApprovalCountdown(() => 1_000_000 + 90_000, active)
    expect(remainingText.value).toBe('01:30')
  })
})
