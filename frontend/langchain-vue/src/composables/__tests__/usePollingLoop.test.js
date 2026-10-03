import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { effectScope } from 'vue'

vi.mock('@/utils/logger', () => ({
  logger: {
    log: vi.fn(),
    warn: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    debug: vi.fn(),
    group: vi.fn(),
    groupEnd: vi.fn(),
  },
}))

import { usePollingLoop } from '../usePollingLoop'

/** 测试默认轮询参数：基础 1s / 上限 10s / 2 倍退避 */
const DEFAULT_OPTIONS = { interval: 1000, backoff: { maxDelay: 10000, multiplier: 2 } }

/**
 * 在 effectScope 内实例化 usePollingLoop（模拟组件 setup 上下文，
 * 使 onScopeDispose 清理钩子可经 scope.stop() 触发）
 */
function setupLoop(overrides = {}) {
  const tick = overrides.tick ?? vi.fn(async () => {})
  const shouldStop = overrides.shouldStop ?? vi.fn(() => false)
  const scope = effectScope()
  let ctx
  scope.run(() => {
    ctx = usePollingLoop({ ...DEFAULT_OPTIONS, ...overrides, tick, shouldStop })
  })
  return { tick, shouldStop, scope, ctx }
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('usePollingLoop - tick 正常自续', () => {
  it('按基础间隔持续自续：每轮 tick 结束后按当前间隔调度下一轮', async () => {
    const { tick, ctx } = setupLoop()

    ctx.startPolling()
    expect(ctx.pollingTimer.value).not.toBeNull()

    await vi.advanceTimersByTimeAsync(1000)
    expect(tick).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(1000)
    expect(tick).toHaveBeenCalledTimes(2)

    await vi.advanceTimersByTimeAsync(3000)
    expect(tick).toHaveBeenCalledTimes(5)
  })

  it('pollNow 立即执行一轮并恢复自续，且取消已排程的一轮（单链不双跑）', async () => {
    const { tick, ctx } = setupLoop()

    ctx.startPolling()
    await ctx.pollNow()
    expect(tick).toHaveBeenCalledTimes(1)

    // 若 pollNow 未取消已排程轮，此处会双链触发 3 次
    await vi.advanceTimersByTimeAsync(1000)
    expect(tick).toHaveBeenCalledTimes(2)
  })

  it('pollNow 可独立启动轮询（无需先 startPolling）', async () => {
    const { tick, ctx } = setupLoop()

    await ctx.pollNow()
    expect(tick).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(1000)
    expect(tick).toHaveBeenCalledTimes(2)
  })

  it('tick 内经 stepBackoff/resetBackoff 调整节奏，生效于下一轮间隔', async () => {
    let round = 0
    const { tick, ctx } = setupLoop()
    tick.mockImplementation(async () => {
      round++
      if (round === 1) ctx.stepBackoff() // 1000 → 2000
      if (round === 2) ctx.resetBackoff() // 2000 → 1000
    })

    ctx.startPolling()
    await vi.advanceTimersByTimeAsync(1000)
    expect(tick).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(1999)
    expect(tick).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(tick).toHaveBeenCalledTimes(2)

    await vi.advanceTimersByTimeAsync(1000)
    expect(tick).toHaveBeenCalledTimes(3)
  })
})

describe('usePollingLoop - tick 抛错经 backoff 自续', () => {
  it('tick 抛错不中断轮询：经一步退避（multiplier）后自续', async () => {
    const tick = vi.fn(async () => { throw new Error('boom') })
    const { ctx } = setupLoop({ tick })

    ctx.startPolling()
    await vi.advanceTimersByTimeAsync(1000)
    expect(tick).toHaveBeenCalledTimes(1)

    // floor(1000 × 2) = 2000
    await vi.advanceTimersByTimeAsync(1999)
    expect(tick).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(tick).toHaveBeenCalledTimes(2)

    // floor(2000 × 2) = 4000
    await vi.advanceTimersByTimeAsync(4000)
    expect(tick).toHaveBeenCalledTimes(3)
  })

  it('连续抛错退避封顶 maxDelay', async () => {
    const tick = vi.fn(async () => { throw new Error('boom') })
    const { ctx } = setupLoop({ tick })

    ctx.startPolling()
    await vi.advanceTimersByTimeAsync(1000)
    expect(ctx.currentDelay.value).toBe(2000)

    await vi.advanceTimersByTimeAsync(2000)
    expect(ctx.currentDelay.value).toBe(4000)

    await vi.advanceTimersByTimeAsync(4000)
    expect(ctx.currentDelay.value).toBe(8000)

    await vi.advanceTimersByTimeAsync(8000)
    expect(ctx.currentDelay.value).toBe(10000)

    await vi.advanceTimersByTimeAsync(10000)
    expect(ctx.currentDelay.value).toBe(10000)
  })

  it('stopPolling 重置退避间隔回基础值', async () => {
    const tick = vi.fn(async () => { throw new Error('boom') })
    const { ctx } = setupLoop({ tick })

    ctx.startPolling()
    await vi.advanceTimersByTimeAsync(1000)
    expect(ctx.currentDelay.value).toBe(2000)

    ctx.stopPolling()
    expect(ctx.currentDelay.value).toBe(1000)
  })
})

describe('usePollingLoop - shouldStop 终止', () => {
  it('shouldStop=true 时本轮 tick 不执行并停止轮询', async () => {
    const { tick, ctx } = setupLoop({ shouldStop: () => true })

    ctx.startPolling()
    await vi.advanceTimersByTimeAsync(5000)

    expect(tick).not.toHaveBeenCalled()
    expect(ctx.pollingTimer.value).toBeNull()
  })

  it('shouldStop 动态转真：当前轮完成后停止，不再触发后续轮次', async () => {
    let rounds = 0
    const { tick, ctx } = setupLoop({ shouldStop: () => rounds >= 2 })
    tick.mockImplementation(async () => { rounds++ })

    ctx.startPolling()
    await vi.advanceTimersByTimeAsync(1000)
    expect(tick).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(1000)
    expect(tick).toHaveBeenCalledTimes(2)

    // 第 3 轮前 shouldStop 命中，终止
    await vi.advanceTimersByTimeAsync(1000)
    expect(tick).toHaveBeenCalledTimes(2)

    await vi.advanceTimersByTimeAsync(10000)
    expect(tick).toHaveBeenCalledTimes(2)
  })
})

describe('usePollingLoop - stopPolling 后不再触发', () => {
  it('清除已排程的下一轮定时器', async () => {
    const { tick, ctx } = setupLoop()

    ctx.startPolling()
    await vi.advanceTimersByTimeAsync(1000)
    expect(tick).toHaveBeenCalledTimes(1)

    ctx.stopPolling()
    expect(ctx.pollingTimer.value).toBeNull()

    await vi.advanceTimersByTimeAsync(10000)
    expect(tick).toHaveBeenCalledTimes(1)
  })

  it('tick 进行中 stopPolling：tick resolve 后不自续', async () => {
    let resolveTick
    const tick = vi.fn(() => new Promise(resolve => { resolveTick = resolve }))
    const { ctx } = setupLoop({ tick })

    ctx.startPolling()
    await vi.advanceTimersByTimeAsync(1000)
    expect(tick).toHaveBeenCalledTimes(1)

    ctx.stopPolling()
    resolveTick()
    await vi.advanceTimersByTimeAsync(10000)

    expect(tick).toHaveBeenCalledTimes(1)
  })

  it('stopPolling 幂等：重复调用不抛错', () => {
    const { ctx } = setupLoop()

    expect(() => {
      ctx.stopPolling()
      ctx.stopPolling()
    }).not.toThrow()
    expect(ctx.pollingTimer.value).toBeNull()
  })
})

describe('usePollingLoop - 组件卸载自动清理（effectScope dispose）', () => {
  it('scope.stop() 后轮询停止且不再自续', async () => {
    const { tick, scope, ctx } = setupLoop()

    ctx.startPolling()
    await vi.advanceTimersByTimeAsync(1000)
    expect(tick).toHaveBeenCalledTimes(1)

    scope.stop()
    expect(ctx.pollingTimer.value).toBeNull()

    await vi.advanceTimersByTimeAsync(10000)
    expect(tick).toHaveBeenCalledTimes(1)
  })

  it('scope dispose 前已退避的间隔被重置', async () => {
    const tick = vi.fn(async () => { throw new Error('boom') })
    const { scope, ctx } = setupLoop({ tick })

    ctx.startPolling()
    await vi.advanceTimersByTimeAsync(1000)
    expect(ctx.currentDelay.value).toBe(2000)

    scope.stop()
    expect(ctx.currentDelay.value).toBe(1000)
  })
})

describe('usePollingLoop - interval/backoff 参数注入', () => {
  it('自定义 interval 与 multiplier 逐项生效', async () => {
    const tick = vi.fn(async () => { throw new Error('x') })
    const { ctx } = setupLoop({ tick, interval: 500, backoff: { maxDelay: 4000, multiplier: 3 } })

    ctx.startPolling()
    await vi.advanceTimersByTimeAsync(499)
    expect(tick).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1)
    expect(tick).toHaveBeenCalledTimes(1)

    // floor(500 × 3) = 1500
    expect(ctx.currentDelay.value).toBe(1500)
    await vi.advanceTimersByTimeAsync(1499)
    expect(tick).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(tick).toHaveBeenCalledTimes(2)

    // floor(1500 × 3) = 4500 → 封顶 4000
    expect(ctx.currentDelay.value).toBe(4000)
  })
})
