import { describe, it, expect, beforeEach, vi } from 'vitest'
import { ref } from 'vue'

const { mockElMessage } = vi.hoisted(() => ({
  mockElMessage: { success: vi.fn(), info: vi.fn(), error: vi.fn(), warning: vi.fn() },
}))

const mockStart = vi.fn()
const mockContinueResearch = vi.fn()
const mockGetStatus = vi.fn()
const mockSearchFiles = vi.fn()
vi.mock('@/api', () => ({
  deepResearchAPI: {
    start: (...args) => mockStart(...args),
    continueResearch: (...args) => mockContinueResearch(...args),
    getStatus: (...args) => mockGetStatus(...args),
    searchFiles: (...args) => mockSearchFiles(...args),
  },
}))

vi.mock('element-plus', () => ({ ElMessage: mockElMessage }))

vi.mock('@/utils/logger', () => ({
  logger: { log: () => {}, info: () => {}, warn: () => {}, error: () => {}, debug: () => {} },
}))

import { useResearchTask } from '../useResearchTask'

describe('useResearchTask', () => {
  let task
  let fileBrowserRef
  let modelStore
  let approvalStore
  let router
  let route
  let bridge

  function createTask() {
    const rt = useResearchTask({ task, fileBrowserRef, modelStore, approvalStore, router, route })
    rt.setBridge(bridge)
    return rt
  }

  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers()
    task = ref(null)
    fileBrowserRef = ref({ loadFiles: vi.fn() })
    modelStore = {
      getModelConfig: vi.fn(() => ({
        provider_id: 'p-1',
        model_name: 'm-1',
        temperature: 0.7,
        max_tokens: 4096,
        special_params: {},
      })),
      thinkingEnabled: ref(false),
      selectProvider: vi.fn(),
    }
    approvalStore = {
      pendingApprovals: new Map(),
      clearByTaskId: vi.fn(),
    }
    router = { push: vi.fn() }
    route = { query: {} }
    bridge = {
      connectSSE: vi.fn(),
      closeSSE: vi.fn(),
      stopPolling: vi.fn(),
      resetPolling: vi.fn(),
      subscribeRealtimeForTask: vi.fn(),
      clearRealtimeSubscriptions: vi.fn(),
      checkDocAnalysisFile: vi.fn(),
      autoLoadDocAnalysis: vi.fn(),
    }
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  describe('startResearch', () => {
    it('查询为空时警告且不发起请求', async () => {
      const { startResearch, researchForm } = createTask()
      researchForm.query = '   '
      await startResearch()

      expect(mockElMessage.warning).toHaveBeenCalledWith('请输入研究主题')
      expect(mockStart).not.toHaveBeenCalled()
    })

    it('成功启动任务并接入 SSE / 实时订阅 / 计时器', async () => {
      mockStart.mockResolvedValue({ data: { data: { task_id: 't-1', status: 'pending' } } })
      const { startResearch, researchForm, isLoading, showTaskDetail } = createTask()
      researchForm.query = '研究 LangChain'
      researchForm.knowledge_base_ids = ['kb-1']

      const promise = startResearch()
      expect(isLoading.value).toBe(true)
      await promise

      expect(isLoading.value).toBe(false)
      expect(mockStart).toHaveBeenCalled()
      // start 请求参数携带 enable_doc_analysis（因 knowledge_base_ids 非空）
      const payload = mockStart.mock.calls[0][0]
      expect(payload.enable_doc_analysis).toBe(true)
      expect(payload.knowledge_base_ids).toEqual(['kb-1'])
      expect(task.value.task_id).toBe('t-1')
      expect(showTaskDetail.value).toBe(true)
      expect(mockElMessage.success).toHaveBeenCalledWith('研究任务已启动')
      expect(bridge.stopPolling).toHaveBeenCalled()
      expect(bridge.subscribeRealtimeForTask).toHaveBeenCalledWith(task.value)
      expect(bridge.connectSSE).toHaveBeenCalledWith('t-1')
    })

    it('启动失败时提示错误', async () => {
      mockStart.mockRejectedValue({
        response: { data: { data: '后端错误详情' } },
      })
      const { startResearch, researchForm, isLoading } = createTask()
      researchForm.query = 'topic'

      await startResearch()

      expect(isLoading.value).toBe(false)
      expect(mockElMessage.error).toHaveBeenCalledWith('后端错误详情')
    })

    it('启动失败无 detail 时使用默认提示', async () => {
      mockStart.mockRejectedValue(new Error('network'))
      const { startResearch, researchForm } = createTask()
      researchForm.query = 'topic'

      await startResearch()

      expect(mockElMessage.error).toHaveBeenCalledWith('启动研究任务失败，请稍后重试')
    })
  })

  describe('viewTask', () => {
    it('running 任务连接 SSE 并订阅实时事件', async () => {
      const { viewTask } = createTask()
      await viewTask({ task_id: 't-1', status: 'running' })

      expect(task.value.task_id).toBe('t-1')
      expect(bridge.closeSSE).toHaveBeenCalled()
      expect(bridge.stopPolling).toHaveBeenCalled()
      expect(bridge.checkDocAnalysisFile).toHaveBeenCalled()
      expect(bridge.subscribeRealtimeForTask).toHaveBeenCalled()
      expect(bridge.connectSSE).toHaveBeenCalledWith('t-1')
    })

    it('completed 任务获取最新状态并刷新文件', async () => {
      mockGetStatus.mockResolvedValue({ data: { data: { task_id: 't-1', status: 'completed', final_report: 'report' } } })
      const { viewTask } = createTask()

      await viewTask({ task_id: 't-1', status: 'completed' })

      expect(mockGetStatus).toHaveBeenCalledWith('t-1')
      expect(task.value.final_report).toBe('report')
      // completed 时不连接 SSE
      expect(bridge.connectSSE).not.toHaveBeenCalled()
    })

    it('getStatus 失败时记录警告但不抛错', async () => {
      mockGetStatus.mockRejectedValue(new Error('获取状态失败'))
      const { viewTask } = createTask()

      await expect(viewTask({ task_id: 't-1', status: 'completed' })).resolves.toBeUndefined()
    })
  })

  describe('deleteTask', () => {
    it('清理 SSE / 轮询 / 实时订阅 / 审批', () => {
      task.value = { task_id: 't-1' }
      const { deleteTask, showTaskDetail } = createTask()
      showTaskDetail.value = true

      deleteTask()

      expect(bridge.stopPolling).toHaveBeenCalled()
      expect(bridge.closeSSE).toHaveBeenCalled()
      expect(bridge.clearRealtimeSubscriptions).toHaveBeenCalled()
      expect(approvalStore.clearByTaskId).toHaveBeenCalledWith('t-1')
      expect(task.value).toBeNull()
      expect(showTaskDetail.value).toBe(false)
      expect(bridge.checkDocAnalysisFile).toHaveBeenCalled()
    })
  })

  describe('continue research', () => {
    it('openContinueDialog 设置 continueForm 并选中模型', () => {
      const { openContinueDialog, continueForm, continueDialogVisible, continueParentTask } = createTask()
      const taskData = {
        task_id: 't-1',
        query: '原研究',
        enable_web_search: false,
        knowledge_base_ids: ['kb-1'],
        provider_id: 'p-2',
        model_name: 'm-2',
        use_mcp: true,
        selected_mcp_servers: ['srv-1'],
        selected_tools: ['tool-1'],
        version: 2,
      }

      openContinueDialog(taskData)

      expect(continueParentTask.value).toEqual(taskData)
      expect(continueForm.enable_web_search).toBe(false)
      expect(continueForm.knowledge_base_ids).toEqual(['kb-1'])
      expect(continueForm.provider_id).toBe('p-2')
      expect(continueForm.use_mcp).toBe(true)
      expect(continueForm.selected_tools).toEqual(['tool-1'])
      expect(modelStore.selectProvider).toHaveBeenCalledWith('p-2', 'm-2')
      expect(continueDialogVisible.value).toBe(true)
    })

    it('submitContinueResearch 成功启动续研', async () => {
      mockContinueResearch.mockResolvedValue({ data: { data: { task_id: 't-2', status: 'pending' } } })
      const { submitContinueResearch, continueParentTask, isLoading } = createTask()
      continueParentTask.value = { task_id: 't-1' }

      const promise = submitContinueResearch()
      expect(isLoading.value).toBe(true)
      await promise

      expect(isLoading.value).toBe(false)
      expect(mockContinueResearch).toHaveBeenCalledWith('t-1', expect.any(Object))
      expect(task.value.task_id).toBe('t-2')
      expect(mockElMessage.success).toHaveBeenCalledWith('续研任务已启动')
      expect(bridge.connectSSE).toHaveBeenCalledWith('t-2')
      expect(bridge.subscribeRealtimeForTask).toHaveBeenCalledWith(task.value)
    })

    it('submitContinueResearch 无父任务时直接返回', async () => {
      const { submitContinueResearch } = createTask()
      await submitContinueResearch()
      expect(mockContinueResearch).not.toHaveBeenCalled()
    })

    it('handleContinueTask 委托 openContinueDialog', () => {
      const { handleContinueTask } = createTask()
      handleContinueTask({ task_id: 't-1', provider_id: 'p-1', model_name: 'm-1' })
      // 间接验证：openContinueDialog 已选中模型
      expect(modelStore.selectProvider).toHaveBeenCalledWith('p-1', 'm-1')
    })
  })

  describe('openInChat', () => {
    it('跳转到 /chat 携带任务参数', () => {
      task.value = { task_id: 't-1', query: '研究主题', session_id: 's-1' }
      const { openInChat } = createTask()

      openInChat()

      expect(router.push).toHaveBeenCalledWith({
        path: '/chat',
        query: expect.objectContaining({
          researchTaskId: 't-1',
          session_id: 's-1',
          research_query: '研究主题',
        }),
      })
    })

    it('任务信息不完整时警告', () => {
      task.value = { task_id: 't-1' }
      const { openInChat } = createTask()

      openInChat()

      expect(mockElMessage.warning).toHaveBeenCalledWith('研究任务信息不完整，无法在聊天中讨论')
      expect(router.push).not.toHaveBeenCalled()
    })
  })

  describe('handleFileSearch', () => {
    it('空查询时不搜索', async () => {
      const { handleFileSearch, fileSearchQuery } = createTask()
      fileSearchQuery.value = '   '
      await handleFileSearch()
      expect(mockSearchFiles).not.toHaveBeenCalled()
    })

    it('搜索成功填充结果', async () => {
      mockSearchFiles.mockResolvedValue({
        data: { code: 200, data: { items: [{ filename: 'a.md', task_id: 't-1' }] } },
      })
      const { handleFileSearch, fileSearchQuery, fileSearchResults, fileSearchLoading } = createTask()
      fileSearchQuery.value = '关键词'

      const promise = handleFileSearch()
      expect(fileSearchLoading.value).toBe(true)
      await promise

      expect(fileSearchLoading.value).toBe(false)
      expect(fileSearchResults.value).toHaveLength(1)
      expect(fileSearchResults.value[0].filename).toBe('a.md')
    })

    it('搜索失败时提示错误', async () => {
      mockSearchFiles.mockRejectedValue(new Error('搜索失败'))
      const { handleFileSearch, fileSearchQuery } = createTask()
      fileSearchQuery.value = '关键词'

      await handleFileSearch()

      expect(mockElMessage.error).toHaveBeenCalledWith('文件搜索失败')
    })
  })

  describe('computed', () => {
    it('progressPercentage：task 为 null 时返回 0', () => {
      const { progressPercentage } = createTask()
      expect(progressPercentage.value).toBe(0)
    })

    it('progressPercentage：completed 返回 100', () => {
      task.value = { status: 'completed' }
      const { progressPercentage } = createTask()
      expect(progressPercentage.value).toBe(100)
    })

    it('progressPercentage：pending 返回 10', () => {
      task.value = { status: 'pending' }
      const { progressPercentage } = createTask()
      expect(progressPercentage.value).toBe(10)
    })

    it('progressPercentage：running 基于 elapsedSeconds', () => {
      task.value = { status: 'running' }
      const { progressPercentage, elapsedSeconds, startElapsedTimer } = createTask()
      elapsedSeconds.value = 0
      expect(progressPercentage.value).toBe(10)

      startElapsedTimer()
      vi.advanceTimersByTime(60000) // 60s
      // 60s / 600s * 80 + 10 = 18
      expect(progressPercentage.value).toBe(18)
    })

    it('taskPendingApprovals：过滤当前任务的 deep_research 审批', () => {
      task.value = { task_id: 't-1' }
      approvalStore.pendingApprovals = new Map([
        ['id-1', { source: 'deep_research', taskId: 't-1', approvalData: {} }],
        ['id-2', { source: 'deep_research', taskId: 't-2', approvalData: {} }],
        ['id-3', { source: 'chat', taskId: 't-1', approvalData: {} }],
      ])
      const { taskPendingApprovals } = createTask()

      expect(taskPendingApprovals.value.size).toBe(1)
      expect(taskPendingApprovals.value.has('id-1')).toBe(true)
    })
  })

  describe('model change', () => {
    it('onModelChange 设置 researchForm', () => {
      const { onModelChange, researchForm } = createTask()
      onModelChange({ providerId: 'p-1', modelName: 'm-1' })
      expect(researchForm.provider_id).toBe('p-1')
      expect(researchForm.model_name).toBe('m-1')
    })

    it('onContinueModelChange 设置 continueForm', () => {
      const { onContinueModelChange, continueForm } = createTask()
      onContinueModelChange({ providerId: 'p-2', modelName: 'm-2' })
      expect(continueForm.provider_id).toBe('p-2')
      expect(continueForm.model_name).toBe('m-2')
    })
  })

  describe('elapsed timer', () => {
    it('startElapsedTimer / stopElapsedTimer 控制计时', () => {
      task.value = { status: 'running' }
      const { startElapsedTimer, stopElapsedTimer, elapsedSeconds } = createTask()

      startElapsedTimer()
      vi.advanceTimersByTime(2000)
      expect(elapsedSeconds.value).toBe(2)

      stopElapsedTimer()
      vi.advanceTimersByTime(3000)
      expect(elapsedSeconds.value).toBe(2)
    })
  })

  describe('setBridge', () => {
    it('setBridge 后 bridge 函数可用', async () => {
      mockStart.mockResolvedValue({ data: { data: { task_id: 't-1', status: 'pending' } } })
      const rt = useResearchTask({ task, fileBrowserRef, modelStore, approvalStore, router, route })
      const customConnect = vi.fn()
      rt.setBridge({ ...bridge, connectSSE: customConnect })

      rt.researchForm.query = 'topic'
      await rt.startResearch()

      expect(customConnect).toHaveBeenCalledWith('t-1')
    })
  })
})
