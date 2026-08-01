import { describe, it, expect, beforeEach, vi } from 'vitest'
import { ref } from 'vue'

const { mockElMessage } = vi.hoisted(() => ({
  mockElMessage: { success: vi.fn(), info: vi.fn(), error: vi.fn(), warning: vi.fn() },
}))

let capturedOptions = {}
const mockConnect = vi.fn()
const mockDisconnect = vi.fn()
vi.mock('@/composables/useResearchSSE', () => ({
  useResearchSSE: () => ({
    connect: (...args) => mockConnect(...args),
    disconnect: (...args) => mockDisconnect(...args),
    isConnected: ref(false),
    currentTaskId: ref(null),
    connectionStatus: ref('disconnected'),
  }),
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

import { useResearchStream } from '../useResearchStream'

describe('useResearchStream', () => {
  let task
  let fileBrowserRef
  let stopElapsedTimer
  let checkDocAnalysisFile
  let autoLoadDocAnalysis
  let startPolling

  beforeEach(() => {
    vi.clearAllMocks()
    capturedOptions = {}
    mockConnect.mockImplementation(async (taskId, options) => {
      capturedOptions = options
    })
    mockDisconnect.mockResolvedValue(undefined)
    task = ref({ task_id: 't-1', status: 'running', session_id: 's-1' })
    fileBrowserRef = ref({ loadFiles: vi.fn() })
    stopElapsedTimer = vi.fn()
    checkDocAnalysisFile = vi.fn()
    autoLoadDocAnalysis = vi.fn()
    startPolling = vi.fn()
  })

  function createStream() {
    return useResearchStream({
      task, fileBrowserRef, stopElapsedTimer,
      checkDocAnalysisFile, autoLoadDocAnalysis, startPolling,
    })
  }

  it('connectSSE 调用 sse.connect 并传入回调与 sessionId', async () => {
    const { connectSSE } = createStream()
    await connectSSE('t-1')

    expect(mockConnect).toHaveBeenCalledWith('t-1', expect.objectContaining({
      onStatus: expect.any(Function),
      onMessage: expect.any(Function),
      onError: expect.any(Function),
      onFallbackToPolling: expect.any(Function),
      sessionId: 's-1',
    }))
  })

  it('onStatus completed 更新 task 并刷新文件与文档分析', async () => {
    mockGetStatus.mockResolvedValue({ data: { data: { status: 'completed', final_report: 'fresh' } } })
    const { connectSSE, progressMessage } = createStream()
    await connectSSE('t-1')

    capturedOptions.onStatus({ status: 'completed', message: '完成', final_report: 'report-x' })

    expect(task.value.status).toBe('completed')
    expect(task.value.final_report).toBe('report-x')
    expect(progressMessage.value).toBe('完成')
    expect(stopElapsedTimer).toHaveBeenCalled()
    expect(mockGetStatus).toHaveBeenCalledWith('t-1')
    expect(checkDocAnalysisFile).toHaveBeenCalled()
    expect(fileBrowserRef.value.loadFiles).toHaveBeenCalled()
    expect(autoLoadDocAnalysis).toHaveBeenCalled()
  })

  it('onStatus failed 更新 task 状态', async () => {
    const { connectSSE } = createStream()
    await connectSSE('t-1')

    capturedOptions.onStatus({ status: 'failed', message: '失败' })

    expect(task.value.status).toBe('failed')
    expect(stopElapsedTimer).toHaveBeenCalled()
    // failed 不调用 getStatus 刷新
    expect(mockGetStatus).not.toHaveBeenCalled()
  })

  it('onStatus running 更新进度但不停止', async () => {
    const { connectSSE, progressMessage } = createStream()
    await connectSSE('t-1')

    capturedOptions.onStatus({ status: 'running', message: '执行中' })

    expect(task.value.status).toBe('running')
    expect(progressMessage.value).toBe('执行中')
    expect(stopElapsedTimer).not.toHaveBeenCalled()
  })

  it('onMessage connected 设置进度提示', async () => {
    const { connectSSE, progressMessage } = createStream()
    await connectSSE('t-1')

    capturedOptions.onMessage({ type: 'connected' })
    expect(progressMessage.value).toBe('已连接，等待研究启动...')
  })

  it('onMessage step_update 更新步骤', async () => {
    const { connectSSE, progressMessage } = createStream()
    await connectSSE('t-1')

    capturedOptions.onMessage({ type: 'step_update', step: '搜索中' })
    expect(progressMessage.value).toBe('搜索中')
  })

  it('onMessage done 主动断开并启动轮询（任务未完成）', async () => {
    const { connectSSE } = createStream()
    await connectSSE('t-1')

    capturedOptions.onMessage({ type: 'done' })

    expect(stopElapsedTimer).toHaveBeenCalled()
    expect(mockDisconnect).toHaveBeenCalledWith(true)
    expect(startPolling).toHaveBeenCalled()
  })

  it('onMessage done 任务已完成时不启动轮询', async () => {
    task.value.status = 'completed'
    const { connectSSE } = createStream()
    await connectSSE('t-1')

    capturedOptions.onMessage({ type: 'done' })

    expect(startPolling).not.toHaveBeenCalled()
  })

  it('onMessage timeout 设置超时提示并启动轮询', async () => {
    const { connectSSE, progressMessage } = createStream()
    await connectSSE('t-1')

    capturedOptions.onMessage({ type: 'timeout' })

    expect(progressMessage.value).toBe('连接超时，正在回退到轮询模式...')
    expect(mockDisconnect).toHaveBeenCalledWith(true)
    expect(startPolling).toHaveBeenCalled()
  })

  it('onError 调用 ElMessage.error', async () => {
    const { connectSSE } = createStream()
    await connectSSE('t-1')

    capturedOptions.onError({ message: '执行出错' })

    expect(mockElMessage.error).toHaveBeenCalledWith('执行出错')
    expect(stopElapsedTimer).toHaveBeenCalled()
  })

  it('onFallbackToPolling 启动轮询', async () => {
    const { connectSSE } = createStream()
    await connectSSE('t-1')

    capturedOptions.onFallbackToPolling()

    expect(startPolling).toHaveBeenCalled()
  })

  it('connectSSE 流结束后任务未完成时启动轮询', async () => {
    const { connectSSE } = createStream()
    await connectSSE('t-1')

    // connect resolve 后（流自然结束），任务仍 running，应启动轮询
    expect(mockDisconnect).toHaveBeenCalledWith(true)
    expect(startPolling).toHaveBeenCalled()
  })

  it('connectSSE 流结束后任务已完成时不启动轮询', async () => {
    task.value.status = 'completed'
    const { connectSSE } = createStream()
    await connectSSE('t-1')

    expect(startPolling).not.toHaveBeenCalled()
  })

  it('connectSSE AbortError 时静默返回', async () => {
    const abortError = new Error('aborted')
    abortError.name = 'AbortError'
    mockConnect.mockRejectedValueOnce(abortError)

    const { connectSSE } = createStream()
    await expect(connectSSE('t-1')).resolves.toBeUndefined()

    expect(startPolling).not.toHaveBeenCalled()
  })

  it('connectSSE 非 AbortError 失败时启动轮询', async () => {
    mockConnect.mockRejectedValueOnce(new Error('连接失败'))

    const { connectSSE } = createStream()
    await connectSSE('t-1')

    expect(startPolling).toHaveBeenCalled()
  })

  it('closeSSE 调用 sse.disconnect(true)', () => {
    const { closeSSE } = createStream()
    closeSSE()

    expect(mockDisconnect).toHaveBeenCalledWith(true)
  })

  it('onStatus completed 时 getStatus 失败不抛错', async () => {
    mockGetStatus.mockRejectedValue(new Error('刷新失败'))
    const { connectSSE } = createStream()
    await connectSSE('t-1')

    // 不应抛错（.catch 兜底）
    expect(() => capturedOptions.onStatus({ status: 'completed' })).not.toThrow()
  })
})
