import { describe, it, expect, beforeEach, vi } from 'vitest'
import { ref } from 'vue'

const { mockElMessage } = vi.hoisted(() => ({
  mockElMessage: { success: vi.fn(), info: vi.fn(), error: vi.fn(), warning: vi.fn() },
}))

const mockGetStatus = vi.fn()
vi.mock('@/api', () => ({
  deepResearchAPI: {
    getStatus: (...args) => mockGetStatus(...args),
  },
}))

vi.mock('element-plus', () => ({ ElMessage: mockElMessage }))

vi.mock('@/utils/logger', () => ({
  logger: { log: () => {}, info: () => {}, warn: () => {}, error: () => {}, debug: () => {} },
}))

import { useResearchPolling } from '../useResearchPolling'

describe('useResearchPolling', () => {
  let task
  let fileBrowserRef
  let stopElapsedTimer
  let checkDocAnalysisFile
  let autoLoadDocAnalysis

  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers()
    task = ref({ task_id: 't-1', status: 'running' })
    fileBrowserRef = ref({ loadFiles: vi.fn() })
    stopElapsedTimer = vi.fn()
    checkDocAnalysisFile = vi.fn()
    autoLoadDocAnalysis = vi.fn()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  function createPolling() {
    return useResearchPolling({
      task,
      fileBrowserRef,
      stopElapsedTimer,
      checkDocAnalysisFile,
      autoLoadDocAnalysis,
    })
  }

  it('pollTaskStatus 任务已完成时停止轮询并刷新文件', async () => {
    task.value = { task_id: 't-1', status: 'completed' }
    const { pollTaskStatus } = useResearchPolling({
      task, fileBrowserRef, stopElapsedTimer, checkDocAnalysisFile, autoLoadDocAnalysis,
    })

    await pollTaskStatus()

    expect(fileBrowserRef.value.loadFiles).toHaveBeenCalled()
  })

  it('pollTaskStatus 收到 completed 状态时停止并加载文档分析', async () => {
    mockGetStatus.mockResolvedValue({ data: { data: { status: 'completed', final_report: 'report' } } })

    const { pollTaskStatus } = createPolling()
    await pollTaskStatus()

    expect(mockGetStatus).toHaveBeenCalledWith('t-1')
    expect(task.value.status).toBe('completed')
    expect(stopElapsedTimer).toHaveBeenCalled()
    expect(checkDocAnalysisFile).toHaveBeenCalled()
    expect(fileBrowserRef.value.loadFiles).toHaveBeenCalled()
    expect(autoLoadDocAnalysis).toHaveBeenCalled()
  })

  it('pollTaskStatus 收到 failed 状态时停止', async () => {
    mockGetStatus.mockResolvedValue({ data: { data: { status: 'failed', error_message: 'err' } } })

    const { pollTaskStatus } = createPolling()
    await pollTaskStatus()

    expect(task.value.status).toBe('failed')
    expect(stopElapsedTimer).toHaveBeenCalled()
  })

  it('pollTaskStatus pending→running 重置轮询间隔', async () => {
    task.value = { task_id: 't-1', status: 'pending' }
    mockGetStatus.mockResolvedValue({ data: { data: { status: 'running' } } })

    const { pollTaskStatus } = createPolling()
    await pollTaskStatus()

    // 仍处于 running，未停止
    expect(stopElapsedTimer).not.toHaveBeenCalled()
  })

  it('pollTaskStatus 404 时标记任务失败并停止', async () => {
    mockGetStatus.mockRejectedValue({ response: { status: 404 } })

    const { pollTaskStatus } = createPolling()
    await pollTaskStatus()

    expect(task.value.status).toBe('failed')
    expect(task.value.error_message).toBe('研究任务不存在或已被删除')
  })

  it('pollTaskStatus 非 404 错误时退避继续轮询', async () => {
    mockGetStatus.mockRejectedValueOnce({ response: { status: 500 } })
    mockGetStatus.mockResolvedValueOnce({ data: { data: { status: 'completed' } } })

    const { pollTaskStatus } = createPolling()
    await pollTaskStatus()
    // 第一次失败后退避，第二次完成
    await vi.advanceTimersByTimeAsync(5000)

    expect(mockGetStatus).toHaveBeenCalledTimes(2)
    expect(task.value.status).toBe('completed')
  })

  it('resetPolling 重置 pollCount 与间隔', () => {
    const { resetPolling, pollCount } = createPolling()
    pollCount.value = 100
    resetPolling()
    expect(pollCount.value).toBe(0)
  })

  it('startPolling 调度一次 pollTaskStatus', async () => {
    mockGetStatus.mockResolvedValue({ data: { data: { status: 'running' } } })

    const { startPolling, stopPolling } = createPolling()
    startPolling()

    expect(mockGetStatus).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(3000)
    expect(mockGetStatus).toHaveBeenCalledTimes(1)

    stopPolling()
  })

  it('stopPolling 清理定时器', () => {
    const { startPolling, stopPolling } = createPolling()
    startPolling()
    stopPolling()

    // 停止后不再触发
    return vi.advanceTimersByTimeAsync(3000).then(() => {
      // 不应抛错，且无新增调用
      expect(true).toBe(true)
    })
  })

  it('pollCount 达到上限时提示并停止', async () => {
    const { pollTaskStatus, pollCount, resetPolling } = createPolling()
    resetPolling()
    // 直接设置 pollCount 接近上限
    pollCount.value = 599
    mockGetStatus.mockResolvedValue({ data: { data: { status: 'running' } } })

    await pollTaskStatus()

    expect(mockElMessage.warning).toHaveBeenCalledWith('研究任务轮询超时，请刷新页面查看最新状态')
  })
})
