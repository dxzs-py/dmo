import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

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

import { logger } from '@/utils/logger'
import { useStreamSyncPolling } from '../useStreamSyncPolling'

beforeEach(() => {
  vi.clearAllMocks()
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('useStreamSyncPolling - 周期执行与失败留痕', () => {
  it('按 intervalMs 周期执行同步任务', async () => {
    const syncTask = vi.fn(async () => {})
    const { startStreamSyncPolling, stopStreamSyncPolling } = useStreamSyncPolling(syncTask, { intervalMs: 1000 })

    startStreamSyncPolling()
    await vi.advanceTimersByTimeAsync(3500)
    expect(syncTask).toHaveBeenCalledTimes(3)

    stopStreamSyncPolling()
  })

  it('同步任务失败时 logger.debug 留痕且不中断轮询', async () => {
    const error = new Error('sync failed')
    const syncTask = vi.fn(async () => { throw error })
    const { startStreamSyncPolling, stopStreamSyncPolling } = useStreamSyncPolling(syncTask, { intervalMs: 1000 })

    startStreamSyncPolling()
    await vi.advanceTimersByTimeAsync(1000)
    expect(logger.debug).toHaveBeenCalledWith('[StreamSyncPolling] 同步任务执行失败（保留轮询）:', error)

    // 失败后下一周期仍执行（轮询不中断）
    await vi.advanceTimersByTimeAsync(1000)
    expect(syncTask).toHaveBeenCalledTimes(2)

    stopStreamSyncPolling()
  })

  it('stopStreamSyncPolling 后不再执行', async () => {
    const syncTask = vi.fn(async () => {})
    const { startStreamSyncPolling, stopStreamSyncPolling } = useStreamSyncPolling(syncTask, { intervalMs: 1000 })

    startStreamSyncPolling()
    await vi.advanceTimersByTimeAsync(1000)
    expect(syncTask).toHaveBeenCalledTimes(1)

    stopStreamSyncPolling()
    await vi.advanceTimersByTimeAsync(10000)
    expect(syncTask).toHaveBeenCalledTimes(1)
  })
})
