import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { ref, reactive, nextTick, effectScope } from 'vue'
import { setActivePinia, createPinia } from 'pinia'

const { mockElMessage } = vi.hoisted(() => ({
  mockElMessage: { success: vi.fn(), info: vi.fn(), error: vi.fn(), warning: vi.fn() },
}))

const mockStart = vi.fn()
const mockGetState = vi.fn()
const mockStreamFetch = vi.fn()
vi.mock('@/api', () => ({
  workflowAPI: {
    start: (...args) => mockStart(...args),
    getState: (...args) => mockGetState(...args),
    streamFetch: (...args) => mockStreamFetch(...args),
  },
}))

const mockReadSSEStream = vi.fn()
vi.mock('@/utils/sse', () => ({
  readSSEStream: (...args) => mockReadSSEStream(...args),
}))

const mockSubscribeTask = vi.fn()
const mockSubscribeSession = vi.fn()
vi.mock('@/composables/useRealtimeSync', () => ({
  useRealtimeSync: () => ({
    subscribeTask: (...args) => mockSubscribeTask(...args),
    subscribeSession: (...args) => mockSubscribeSession(...args),
  }),
}))

vi.mock('@/stores/sync', () => ({
  useSyncStore: () => ({ handleRealtimeEvent: vi.fn() }),
}))

vi.mock('element-plus', () => ({ ElMessage: mockElMessage }))

vi.mock('@/utils/logger', () => ({
  logger: { log: () => {}, info: () => {}, warn: () => {}, error: () => {}, debug: () => {} },
}))

// 使用真实 useWorkflowStore：其内部 workflows 为 reactive Map，
// 使 workflowStateFromStore computed 能正确追踪 store 变化，验证 watcher 主路径
import { useWorkflowExecution } from '../useWorkflowExecution'
import { useWorkflowStore } from '@/stores/workflow'

describe('useWorkflowExecution', () => {
  let execution
  let fileBrowserRef
  let workflowForm
  let initAnswersFromQuiz
  let resetAnswers
  let autoLoadKeyFile
  let clearAutoLoad
  let workflowStore
  let scope

  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.useFakeTimers()
    execution = ref(null)
    fileBrowserRef = ref({ loadFiles: vi.fn() })
    workflowForm = reactive({ query: '', knowledge_base_ids: [] })
    initAnswersFromQuiz = vi.fn()
    resetAnswers = vi.fn()
    autoLoadKeyFile = vi.fn()
    clearAutoLoad = vi.fn()
    workflowStore = useWorkflowStore()
    mockSubscribeTask.mockImplementation(() => () => {})
    mockSubscribeSession.mockImplementation(() => () => {})
    mockReadSSEStream.mockResolvedValue(undefined)
    scope = effectScope()
  })

  afterEach(() => {
    scope.stop()
    vi.useRealTimers()
  })

  /** 在 effectScope 内创建 composable，确保 watcher 随 scope 释放 */
  function createExecution() {
    return scope.run(() => useWorkflowExecution({
      execution,
      fileBrowserRef,
      workflowForm,
      initAnswersFromQuiz,
      resetAnswers,
      autoLoadKeyFile,
      clearAutoLoad,
    }))
  }

  describe('startWorkflow', () => {
    it('学习主题为空时提示并中止', async () => {
      const { startWorkflow } = createExecution()
      await startWorkflow()
      expect(mockElMessage.warning).toHaveBeenCalledWith('请输入学习主题')
      expect(mockStart).not.toHaveBeenCalled()
    })

    it('启动成功：设置 execution / showDetail / 订阅实时事件 / 连接 SSE', async () => {
      mockStart.mockResolvedValue({ data: { data: { thread_id: 'wf-1', current_step: 'start' } } })
      const { startWorkflow, isLoading, showDetail } = createExecution()
      workflowForm.query = '学习 Vue3'

      await startWorkflow()

      expect(mockStart).toHaveBeenCalledWith(workflowForm)
      expect(execution.value).toEqual({ thread_id: 'wf-1', current_step: 'start' })
      expect(showDetail.value).toBe(true)
      expect(isLoading.value).toBe(false)
      expect(resetAnswers).toHaveBeenCalled()
      expect(mockSubscribeTask).toHaveBeenCalledWith('wf-1', expect.any(Function), { replayFromSeq: 0 })
      expect(mockStreamFetch).toHaveBeenCalledWith('wf-1', expect.objectContaining({ signal: expect.any(Object) }))
      expect(mockElMessage.success).toHaveBeenCalledWith('工作流已启动')
    })

    it('启动失败时显示错误消息', async () => {
      mockStart.mockRejectedValue(new Error('服务端错误'))
      const { startWorkflow, isLoading } = createExecution()
      workflowForm.query = '学习 Python'

      await startWorkflow()

      expect(execution.value).toBeNull()
      expect(isLoading.value).toBe(false)
      expect(mockElMessage.error).toHaveBeenCalledWith('启动工作流失败，请稍后重试')
    })
  })

  describe('pollExecutionStatus', () => {
    it('execution 为 null 时停止轮询', async () => {
      const { pollExecutionStatus } = createExecution()
      await pollExecutionStatus()
      expect(mockGetState).not.toHaveBeenCalled()
    })

    it('waiting_for_answers 终态时停止轮询（不加载文件）', async () => {
      execution.value = { thread_id: 'wf-1', current_step: 'waiting_for_answers' }
      const { pollExecutionStatus } = createExecution()
      await pollExecutionStatus()
      expect(mockGetState).not.toHaveBeenCalled()
      expect(fileBrowserRef.value.loadFiles).not.toHaveBeenCalled()
    })

    it('end 终态时停止轮询并加载文件与学习资料', async () => {
      execution.value = { thread_id: 'wf-1', current_step: 'end' }
      const { pollExecutionStatus } = createExecution()
      await pollExecutionStatus()
      expect(mockGetState).not.toHaveBeenCalled()
      expect(fileBrowserRef.value.loadFiles).toHaveBeenCalled()
      expect(autoLoadKeyFile).toHaveBeenCalled()
    })

    it('成功获取状态后合并到 execution 并初始化答题表单', async () => {
      execution.value = { thread_id: 'wf-1', current_step: 'planner' }
      mockGetState.mockResolvedValue({ data: { data: { current_step: 'retrieval', quiz: { questions: [] } } } })
      const { pollExecutionStatus } = createExecution()
      await pollExecutionStatus()

      expect(mockGetState).toHaveBeenCalledWith('wf-1')
      expect(execution.value.current_step).toBe('retrieval')
      expect(initAnswersFromQuiz).toHaveBeenCalledWith({ questions: [] })
    })

    it('步骤未变化时按指数退避继续轮询', async () => {
      execution.value = { thread_id: 'wf-1', current_step: 'planner' }
      mockGetState.mockResolvedValue({ data: { data: { current_step: 'planner' } } })
      const { pollExecutionStatus } = createExecution()
      await pollExecutionStatus()

      // 步骤未变，调度下一次轮询（退避后间隔 > 基础间隔）
      expect(mockGetState).toHaveBeenCalledTimes(1)
      await vi.advanceTimersByTimeAsync(5000)
      expect(mockGetState).toHaveBeenCalledTimes(2)
    })

    it('步骤变化时重置轮询间隔为基础值', async () => {
      execution.value = { thread_id: 'wf-1', current_step: 'planner' }
      mockGetState.mockResolvedValue({ data: { data: { current_step: 'retrieval' } } })
      const { pollExecutionStatus } = createExecution()
      await pollExecutionStatus()
      // 步骤变化，currentPollInterval 重置为 BASE(3000)
      await vi.advanceTimersByTimeAsync(3000)
      expect(mockGetState).toHaveBeenCalledTimes(2)
    })

    it('getState 失败时退避继续轮询', async () => {
      execution.value = { thread_id: 'wf-1', current_step: 'planner' }
      mockGetState.mockRejectedValue(new Error('网络错误'))
      const { pollExecutionStatus } = createExecution()
      await pollExecutionStatus()
      expect(mockGetState).toHaveBeenCalledTimes(1)
      // 退避后再次轮询
      await vi.advanceTimersByTimeAsync(5000)
      expect(mockGetState).toHaveBeenCalledTimes(2)
    })
  })

  describe('stopPolling', () => {
    it('清理轮询定时器，后续不再触发', async () => {
      execution.value = { thread_id: 'wf-1', current_step: 'planner' }
      mockGetState.mockResolvedValue({ data: { data: { current_step: 'planner' } } })
      const { pollExecutionStatus, stopPolling } = createExecution()
      await pollExecutionStatus()
      stopPolling()
      await vi.advanceTimersByTimeAsync(60000)
      expect(mockGetState).toHaveBeenCalledTimes(1)
    })
  })

  describe('connectSSE / handleSSEEvent', () => {
    it('connectSSE 调用 streamFetch 与 readSSEStream', async () => {
      execution.value = { thread_id: 'wf-1', current_step: 'planner' }
      mockStreamFetch.mockResolvedValue({ ok: true })
      const { connectSSE } = createExecution()
      await connectSSE('wf-1')
      expect(mockStreamFetch).toHaveBeenCalledWith('wf-1', expect.objectContaining({ signal: expect.any(Object) }))
      expect(mockReadSSEStream).toHaveBeenCalled()
    })

    it('connectSSE 非 ok 响应时抛错并回退到轮询', async () => {
      execution.value = { thread_id: 'wf-1', current_step: 'planner' }
      mockStreamFetch.mockResolvedValue({ ok: false, status: 500, text: async () => 'Internal Server Error' })
      const { connectSSE } = createExecution()
      await connectSSE('wf-1')
      // 回退轮询：调度 pollExecutionStatus
      await vi.advanceTimersByTimeAsync(3000)
      expect(mockGetState).toHaveBeenCalled()
    })

    it('connectSSE AbortError 时静默返回（不回退轮询）', async () => {
      execution.value = { thread_id: 'wf-1', current_step: 'planner' }
      const abortErr = new Error('aborted')
      abortErr.name = 'AbortError'
      mockStreamFetch.mockRejectedValue(abortErr)
      const { connectSSE } = createExecution()
      await expect(connectSSE('wf-1')).resolves.toBeUndefined()
      await vi.advanceTimersByTimeAsync(60000)
      expect(mockGetState).not.toHaveBeenCalled()
    })

    it('handleSSEEvent workflow_step 更新步骤消息与 current_step', () => {
      execution.value = { thread_id: 'wf-1', current_step: 'start' }
      const { handleSSEEvent, currentStepMessage } = createExecution()
      handleSSEEvent({ type: 'workflow_step', data: { step: 'planner', message: '生成学习计划中' } })
      expect(execution.value.current_step).toBe('planner')
      expect(currentStepMessage.value).toBe('生成学习计划中')
    })

    it('handleSSEEvent workflow_step 无 message 时使用默认文案', () => {
      execution.value = { thread_id: 'wf-1', current_step: 'start' }
      const { handleSSEEvent, currentStepMessage } = createExecution()
      handleSSEEvent({ type: 'workflow_step', data: { step: 'planner' } })
      expect(currentStepMessage.value).toBe('工作流启动中...')
    })

    it('handleSSEEvent workflow_state_update 合并部分字段', () => {
      execution.value = { thread_id: 'wf-1', current_step: 'planner', score: null }
      const { handleSSEEvent } = createExecution()
      handleSSEEvent({
        type: 'workflow_state_update',
        data: { current_step: 'grading', score: 80, feedback: '良好' },
      })
      expect(execution.value.current_step).toBe('grading')
      expect(execution.value.score).toBe(80)
      expect(execution.value.feedback).toBe('良好')
    })

    it('handleSSEEvent waiting_for_answers 时关闭 SSE 并初始化答题表单', () => {
      execution.value = { thread_id: 'wf-1', current_step: 'quiz_generator' }
      const { handleSSEEvent } = createExecution()
      const quiz = { questions: [{ id: 'q1' }] }
      handleSSEEvent({
        type: 'workflow_state_update',
        data: { state: 'waiting_for_answers', quiz },
      })
      expect(execution.value.state).toBe('waiting_for_answers')
      expect(initAnswersFromQuiz).toHaveBeenCalledWith(quiz)
    })

    it('handleSSEEvent workflow_completed 关闭 SSE 并加载文件与学习资料', () => {
      execution.value = { thread_id: 'wf-1', current_step: 'feedback' }
      const { handleSSEEvent } = createExecution()
      handleSSEEvent({ type: 'workflow_completed', data: { score: 100 } })
      expect(execution.value.status).toBe('completed')
      expect(execution.value.score).toBe(100)
      expect(fileBrowserRef.value.loadFiles).toHaveBeenCalled()
      expect(autoLoadKeyFile).toHaveBeenCalled()
    })

    it('handleSSEEvent workflow_failed 显示错误消息', () => {
      execution.value = { thread_id: 'wf-1', current_step: 'grading' }
      const { handleSSEEvent } = createExecution()
      handleSSEEvent({ type: 'workflow_failed', data: { error: '评分服务异常' } })
      expect(execution.value.status).toBe('failed')
      expect(mockElMessage.error).toHaveBeenCalledWith('评分服务异常')
    })

    it('handleSSEEvent workflow_failed 无 error 时使用默认消息', () => {
      execution.value = { thread_id: 'wf-1', current_step: 'grading' }
      const { handleSSEEvent } = createExecution()
      handleSSEEvent({ type: 'workflow_failed', data: {} })
      expect(mockElMessage.error).toHaveBeenCalledWith('工作流执行失败')
    })

    it('handleSSEEvent error 事件显示消息并关闭 SSE', () => {
      execution.value = { thread_id: 'wf-1', current_step: 'planner' }
      const { handleSSEEvent } = createExecution()
      handleSSEEvent({ type: 'error', message: '执行出错' })
      expect(mockElMessage.error).toHaveBeenCalledWith('执行出错')
    })
  })

  describe('subscribeRealtimeForTask / clearRealtimeSubscriptions', () => {
    it('为 task + session 订阅（以 thread_id 作为任务标识）', () => {
      const { subscribeRealtimeForTask, realtimeUnsubscribers } = createExecution()
      subscribeRealtimeForTask({ thread_id: 'wf-1', chat_session_id: 's-1' })
      expect(mockSubscribeTask).toHaveBeenCalledWith('wf-1', expect.any(Function), { replayFromSeq: 0 })
      expect(mockSubscribeSession).toHaveBeenCalledWith('s-1', expect.any(Function), { replayFromSeq: 0 })
      expect(realtimeUnsubscribers.value).toHaveLength(2)
    })

    it('无 session 时仅订阅 task', () => {
      const { subscribeRealtimeForTask, realtimeUnsubscribers } = createExecution()
      subscribeRealtimeForTask({ thread_id: 'wf-1' })
      expect(mockSubscribeTask).toHaveBeenCalledWith('wf-1', expect.any(Function), { replayFromSeq: 0 })
      expect(mockSubscribeSession).not.toHaveBeenCalled()
      expect(realtimeUnsubscribers.value).toHaveLength(1)
    })

    it('入参为 null 时直接返回', () => {
      const { subscribeRealtimeForTask } = createExecution()
      subscribeRealtimeForTask(null)
      expect(mockSubscribeTask).not.toHaveBeenCalled()
    })

    it('幂等：同 thread_id + session 跳过', () => {
      const { subscribeRealtimeForTask } = createExecution()
      subscribeRealtimeForTask({ thread_id: 'wf-1', session_id: 's-1' })
      subscribeRealtimeForTask({ thread_id: 'wf-1', session_id: 's-1' })
      expect(mockSubscribeTask).toHaveBeenCalledTimes(1)
      expect(mockSubscribeSession).toHaveBeenCalledTimes(1)
    })

    it('clearRealtimeSubscriptions 调用所有 unsubscribe 函数', () => {
      const unsubTask = vi.fn()
      const unsubSession = vi.fn()
      mockSubscribeTask.mockReturnValue(unsubTask)
      mockSubscribeSession.mockReturnValue(unsubSession)
      const { subscribeRealtimeForTask, clearRealtimeSubscriptions, realtimeUnsubscribers } = createExecution()
      subscribeRealtimeForTask({ thread_id: 'wf-1', chat_session_id: 's-1' })
      expect(realtimeUnsubscribers.value).toHaveLength(2)
      clearRealtimeSubscriptions()
      expect(unsubTask).toHaveBeenCalled()
      expect(unsubSession).toHaveBeenCalled()
      expect(realtimeUnsubscribers.value).toHaveLength(0)
    })
  })

  describe('viewTask', () => {
    it('活跃任务（running）连接 SSE 并订阅实时事件', async () => {
      execution.value = null
      mockStreamFetch.mockResolvedValue({ ok: true })
      const { viewTask, showDetail } = createExecution()
      const task = { thread_id: 'wf-1', status: 'running', current_step: 'planner' }
      await viewTask(task)
      expect(execution.value).toEqual(task)
      expect(showDetail.value).toBe(true)
      expect(clearAutoLoad).toHaveBeenCalled()
      expect(mockSubscribeTask).toHaveBeenCalledWith('wf-1', expect.any(Function), { replayFromSeq: 0 })
      expect(mockStreamFetch).toHaveBeenCalledWith('wf-1', expect.anything())
    })

    it('非活跃任务拉取最新状态，已完成时加载文件与学习资料', async () => {
      execution.value = null
      mockGetState.mockResolvedValue({ data: { data: { current_step: 'completed', status: 'completed' } } })
      const { viewTask } = createExecution()
      const task = { thread_id: 'wf-1', status: 'completed', current_step: 'end' }
      await viewTask(task)
      expect(mockGetState).toHaveBeenCalledWith('wf-1')
      expect(execution.value.status).toBe('completed')
      await nextTick()
      expect(fileBrowserRef.value.loadFiles).toHaveBeenCalled()
      expect(autoLoadKeyFile).toHaveBeenCalled()
    })

    it('非活跃任务拉取最新状态为活跃时连接 SSE', async () => {
      execution.value = null
      mockStreamFetch.mockResolvedValue({ ok: true })
      mockGetState.mockResolvedValue({ data: { data: { current_step: 'grading', thread_id: 'wf-1' } } })
      const { viewTask } = createExecution()
      await viewTask({ thread_id: 'wf-1', status: 'completed', current_step: 'end' })
      expect(mockGetState).toHaveBeenCalledWith('wf-1')
      expect(mockStreamFetch).toHaveBeenCalledWith('wf-1', expect.anything())
    })

    it('携带 quiz 时初始化答题表单', async () => {
      execution.value = null
      const quiz = { questions: [{ id: 'q1' }] }
      mockGetState.mockResolvedValue({ data: { data: { current_step: 'completed', quiz } } })
      const { viewTask } = createExecution()
      await viewTask({ thread_id: 'wf-1', status: 'completed', current_step: 'end', quiz })
      expect(initAnswersFromQuiz).toHaveBeenCalledWith(quiz)
    })

    it('getState 失败时静默处理（不抛错）', async () => {
      execution.value = null
      mockGetState.mockRejectedValue(new Error('获取失败'))
      const { viewTask } = createExecution()
      await expect(viewTask({ thread_id: 'wf-1', status: 'completed', current_step: 'end' })).resolves.toBeUndefined()
    })
  })

  describe('deleteTask', () => {
    it('取消订阅、清理 store、清空 execution', () => {
      execution.value = { thread_id: 'wf-1', current_step: 'planner' }
      const { deleteTask, showDetail } = createExecution()
      deleteTask()
      expect(execution.value).toBeNull()
      expect(showDetail.value).toBe(false)
      expect(resetAnswers).toHaveBeenCalled()
    })

    it('清理 workflowStore 中对应任务的状态', () => {
      execution.value = { thread_id: 'wf-1', current_step: 'planner' }
      workflowStore.setWorkflowState('wf-1', { current_step: 'planner' })
      expect(workflowStore.getWorkflowState('wf-1')).not.toBeNull()
      const { deleteTask } = createExecution()
      deleteTask()
      expect(workflowStore.getWorkflowState('wf-1')).toBeNull()
    })
  })

  describe('resetWorkflow', () => {
    it('清空 execution / 表单 / 答案 / 学习资料，停止轮询与 SSE', () => {
      execution.value = { thread_id: 'wf-1', current_step: 'feedback' }
      workflowForm.query = '旧主题'
      workflowForm.knowledge_base_ids = [1, 2]
      const { resetWorkflow, showDetail } = createExecution()
      resetWorkflow()
      expect(execution.value).toBeNull()
      expect(showDetail.value).toBe(false)
      expect(workflowForm.query).toBe('')
      expect(workflowForm.knowledge_base_ids).toEqual([])
      expect(resetAnswers).toHaveBeenCalled()
      expect(clearAutoLoad).toHaveBeenCalled()
    })

    it('清理 workflowStore 中对应任务的状态', () => {
      execution.value = { thread_id: 'wf-1', current_step: 'feedback' }
      workflowStore.setWorkflowState('wf-1', { current_step: 'feedback' })
      const { resetWorkflow } = createExecution()
      resetWorkflow()
      expect(workflowStore.getWorkflowState('wf-1')).toBeNull()
    })
  })

  describe('backToList', () => {
    it('仅隐藏详情，不清理 execution 与订阅', () => {
      execution.value = { thread_id: 'wf-1', current_step: 'planner' }
      const { backToList, showDetail } = createExecution()
      backToList()
      expect(showDetail.value).toBe(false)
      expect(execution.value).not.toBeNull()
    })
  })

  describe('workflowStore → execution 同步（watcher）', () => {
    it('store 状态变化时合并到 execution', async () => {
      execution.value = { thread_id: 'wf-1', current_step: 'start', status: 'running' }
      createExecution()

      // store 写入新状态 → computed 变化 → watcher 合并到 execution
      workflowStore.setWorkflowState('wf-1', { current_step: 'planner', status: 'running' })
      await nextTick()

      expect(execution.value.current_step).toBe('planner')
    })

    it('完成状态时加载文件与学习资料', async () => {
      execution.value = { thread_id: 'wf-1', current_step: 'feedback', status: 'running' }
      createExecution()

      workflowStore.setWorkflowState('wf-1', { status: 'completed', current_step: 'end' })
      await nextTick()
      // 完成态在 nextTick 中加载文件
      await nextTick()
      expect(fileBrowserRef.value.loadFiles).toHaveBeenCalled()
      expect(autoLoadKeyFile).toHaveBeenCalled()
    })

    it('quiz 变化时初始化答题表单', async () => {
      execution.value = { thread_id: 'wf-1', current_step: 'quiz_generator', status: 'running' }
      createExecution()

      const quiz = { questions: [{ id: 'q1' }] }
      workflowStore.setWorkflowState('wf-1', { quiz, current_step: 'waiting_for_answers' })
      await nextTick()
      expect(initAnswersFromQuiz).toHaveBeenCalledWith(quiz)
    })

    it('失败状态带错误消息时显示 ElMessage.error', async () => {
      execution.value = { thread_id: 'wf-1', current_step: 'grading', status: 'running' }
      createExecution()

      workflowStore.setWorkflowState('wf-1', { status: 'failed', error_message: '后端异常' })
      await nextTick()
      expect(mockElMessage.error).toHaveBeenCalledWith('后端异常')
    })

    it('store 状态字段均未变化时不触发合并', async () => {
      execution.value = { thread_id: 'wf-1', current_step: 'planner', status: 'running' }
      createExecution()
      // 先触发一次同步
      workflowStore.setWorkflowState('wf-1', { current_step: 'planner', status: 'running' })
      await nextTick()
      const stepBefore = execution.value.current_step
      // 再次设置完全相同的状态
      workflowStore.setWorkflowState('wf-1', { current_step: 'planner', status: 'running' })
      await nextTick()
      expect(execution.value.current_step).toBe(stepBefore)
    })
  })
})
