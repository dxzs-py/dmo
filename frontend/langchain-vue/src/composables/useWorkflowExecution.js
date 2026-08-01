import { ref, computed, watch, nextTick } from 'vue'
import { workflowAPI } from '@/api'
import { readSSEStream } from '@/utils/sse'
import { ElMessage } from 'element-plus'
import { logger } from '@/utils/logger'
import { useRealtimeSync } from '@/composables/useRealtimeSync'
import { useSyncStore } from '@/stores/sync'
import { useWorkflowStore } from '@/stores/workflow'

/**
 * 学习工作流执行编排 composable
 *
 * 集中封装工作流执行生命周期：启动 / SSE 流式 / 轮询兜底 / WebSocket 实时同步 / 任务查看与删除。
 * 三路数据源协同语义（与原 WorkflowView 一致，不可变更）：
 *   - SSE：请求浏览器独占的流式数据源，直接更新 execution.value
 *   - 轮询：SSE 不可用或流结束后的兜底，按指数退避轮询 getState
 *   - WebSocket（task 频道）：经 syncStore → workflowStore 写入，本 composable watch store 后
 *     合并到 execution.value，实现跨浏览器同步；与 SSE 双路径幂等
 *
 * 依赖注入（避免循环依赖）：
 *   - 入参 execution 由 view 创建并共享给 useWorkflowSteps / useWorkflowQuiz / useWorkflowFiles
 *   - initAnswersFromQuiz / resetAnswers 来自 useWorkflowQuiz（SSE/轮询/store 收到 quiz 时初始化答案表单）
 *   - autoLoadKeyFile / clearAutoLoad 来自 useWorkflowFiles（完成态加载学习资料、切换任务时清空）
 *
 * @param {Object} deps
 * @param {import('vue').Ref<Object|null>} deps.execution - 当前工作流执行状态 ref（与其它 composable 共享）
 * @param {import('vue').Ref<Object|null>} deps.fileBrowserRef - FileBrowser 组件 ref
 * @param {Object} deps.workflowForm - 启动表单 reactive 对象（query / knowledge_base_ids）
 * @param {(quiz: Object) => void} deps.initAnswersFromQuiz - 初始化答案表单
 * @param {() => void} deps.resetAnswers - 清空答案表单
 * @param {() => Promise<void>} deps.autoLoadKeyFile - 自动加载关键学习资料
 * @param {() => void} deps.clearAutoLoad - 清空学习资料内容
 * @returns {{
 *   isLoading: import('vue').Ref<boolean>,
 *   showDetail: import('vue').Ref<boolean>,
 *   currentStepMessage: import('vue').Ref<string>,
 *   realtimeUnsubscribers: import('vue').Ref<Array<() => void>>,
 *   workflowStateFromStore: import('vue').ComputedRef<Object|null>,
 *   startWorkflow: () => Promise<void>,
 *   pollExecutionStatus: () => Promise<void>,
 *   stopPolling: () => void,
 *   connectSSE: (threadId: string) => Promise<void>,
 *   closeSSE: () => void,
 *   handleSSEEvent: (data: Object) => void,
 *   subscribeRealtimeForTask: (taskObj: Object|null) => void,
 *   clearRealtimeSubscriptions: () => void,
 *   viewTask: (selectedTask: Object) => Promise<void>,
 *   deleteTask: () => void,
 *   resetWorkflow: () => void,
 *   backToList: () => void,
 * }}
 */
export function useWorkflowExecution({
  execution,
  fileBrowserRef,
  workflowForm,
  initAnswersFromQuiz,
  resetAnswers,
  autoLoadKeyFile,
  clearAutoLoad,
}) {
  const realtime = useRealtimeSync()
  const syncStore = useSyncStore()
  const workflowStore = useWorkflowStore()

  const isLoading = ref(false)
  const showDetail = ref(false)
  const currentStepMessage = ref('')

  const BASE_POLL_INTERVAL = 3000
  const MAX_POLL_INTERVAL = 30000
  const POLL_BACKOFF_FACTOR = 1.5

  /** @type {number | null} */
  let pollingTimer = null
  /** @type {AbortController | null} */
  let sseAbortController = null
  let sseReaderActive = false
  let currentPollInterval = BASE_POLL_INTERVAL

  /** @type {import('vue').Ref<Array<() => void>>} */
  const realtimeUnsubscribers = ref([])
  let subscribedTaskId = null
  let subscribedSessionId = null

  // ============================================================================
  // 轮询：SSE 不可用 / 流结束后的兜底，按指数退避轮询 getState
  // ============================================================================

  const stopPolling = () => {
    if (pollingTimer) {
      clearTimeout(pollingTimer)
      pollingTimer = null
    }
    currentPollInterval = BASE_POLL_INTERVAL
  }

  /**
   * 启动一次轮询（递归调度自身）
   * 终态（waiting_for_answers / end / completed）时停止；其余情况按指数退避继续
   */
  const pollExecutionStatus = async () => {
    if (!execution.value) {
      stopPolling()
      return
    }

    const currentStep = execution.value.current_step

    if (currentStep === 'waiting_for_answers' || currentStep === 'end' || currentStep === 'completed') {
      stopPolling()
      if (fileBrowserRef.value && currentStep !== 'waiting_for_answers') {
        fileBrowserRef.value.loadFiles()
        autoLoadKeyFile()
      }
      return
    }

    try {
      const response = await workflowAPI.getState(execution.value.thread_id)
      const data = response.data
      execution.value = { ...execution.value, ...(data.data || data) }

      const responseData = data.data || data
      if (responseData.quiz) {
        initAnswersFromQuiz(responseData.quiz)
      }

      if (currentStep !== execution.value.current_step) {
        currentPollInterval = BASE_POLL_INTERVAL
      } else {
        currentPollInterval = Math.min(
          Math.floor(currentPollInterval * POLL_BACKOFF_FACTOR),
          MAX_POLL_INTERVAL
        )
      }
      pollingTimer = setTimeout(pollExecutionStatus, currentPollInterval)
    } catch (error) {
      logger.error('获取工作流状态失败:', error)
      currentPollInterval = Math.min(
        Math.floor(currentPollInterval * POLL_BACKOFF_FACTOR),
        MAX_POLL_INTERVAL
      )
      pollingTimer = setTimeout(pollExecutionStatus, currentPollInterval)
    }
  }

  // ============================================================================
  // SSE：请求浏览器独占的流式数据源
  // ============================================================================

  const closeSSE = () => {
    sseReaderActive = false
    if (sseAbortController) {
      sseAbortController.abort()
      sseAbortController = null
    }
  }

  /**
   * 处理 SSE 事件（与 task WebSocket 频道的 workflow_* 事件类型对齐，便于双路径统一处理）
   * @param {Object} data - SSE 事件，格式 { type: 'workflow_*', data: {...} }
   */
  const handleSSEEvent = (data) => {
    switch (data.type) {
      case 'workflow_step':
        // 学习工作流节点执行进度（原 'start' 和 'step' 合并）
        currentStepMessage.value = data.data?.message || '工作流启动中...'
        if (execution.value && data.data?.step) {
          execution.value.current_step = data.data.step
        }
        break
      case 'workflow_state_update':
        // 学习工作流状态变更（原 'state_update' 和 'waiting' 合并）
        if (execution.value && data.data) {
          if (data.data.current_step) execution.value.current_step = data.data.current_step
          if (data.data.learning_plan) execution.value.learning_plan = data.data.learning_plan
          if (data.data.retrieved_docs) execution.value.retrieved_docs = data.data.retrieved_docs
          if (data.data.quiz) execution.value.quiz = data.data.quiz
          if (data.data.score !== undefined) execution.value.score = data.data.score
          if (data.data.feedback !== undefined) execution.value.feedback = data.data.feedback
          if (data.data.should_retry !== undefined) execution.value.should_retry = data.data.should_retry
          // waiting_for_answers 状态：原 'waiting' 行为，关闭 SSE 并初始化答题表单
          const isWaiting = data.data.state === 'waiting_for_answers'
            || data.data.current_step === 'waiting_for_answers'
            || data.data.status === 'waiting_for_answers'
          if (isWaiting) {
            execution.value = { ...execution.value, ...data.data }
            if (data.data.quiz) {
              initAnswersFromQuiz(data.data.quiz)
            }
            closeSSE()
          }
        }
        break
      case 'workflow_completed':
        // 学习工作流完成（原 'complete'）
        closeSSE()
        if (execution.value && data.data) {
          execution.value = { ...execution.value, status: 'completed', ...data.data }
        }
        if (fileBrowserRef.value) {
          fileBrowserRef.value.loadFiles()
        }
        autoLoadKeyFile()
        break
      case 'workflow_failed':
        // 学习工作流失败（新增）
        closeSSE()
        if (execution.value && data.data) {
          execution.value = { ...execution.value, status: 'failed', ...data.data }
        }
        ElMessage.error(data.data?.error || data.data?.message || '工作流执行失败')
        break
      case 'stream_error':
        logger.warn('工作流流式执行异常:', data.message || data.data?.message)
        break
      case 'error':
        ElMessage.error(data.message || data.data?.message || '工作流执行出错')
        closeSSE()
        break
    }
  }

  /**
   * 连接 SSE 流
   * - 非 ok 响应：解析 SSE 错误体后抛出，回退到轮询
   * - 流结束后：若任务未到终态，启动轮询兜底
   * - AbortError（主动断开）：静默返回
   * @param {string} threadId
   */
  const connectSSE = async (threadId) => {
    closeSSE()

    sseAbortController = new AbortController()
    sseReaderActive = true

    try {
      const response = await workflowAPI.streamFetch(threadId, {
        signal: sseAbortController.signal,
      })

      if (!response.ok) {
        let errorMsg = `HTTP ${response.status}`
        try {
          const errBody = await response.text()
          const sseMatch = errBody.match(/data:\s*(.*)/)
          if (sseMatch) {
            const parsed = JSON.parse(sseMatch[1])
            errorMsg = parsed.message || parsed.error || errorMsg
          }
        } catch {
          // 解析错误响应失败时使用默认错误消息
        }
        throw new Error(errorMsg)
      }

      await readSSEStream(response, (data) => {
        if (!sseReaderActive) return
        handleSSEEvent(data)
      }, sseAbortController.signal)

      if (sseReaderActive && execution.value) {
        const step = execution.value.current_step
        if (step !== 'waiting_for_answers' && step !== 'end' && step !== 'completed') {
          pollingTimer = setTimeout(pollExecutionStatus, currentPollInterval)
        }
      }
    } catch (error) {
      if (error.name === 'AbortError') {
        return
      }
      logger.error('SSE连接失败，回退到轮询:', error)
      if (execution.value) {
        const step = execution.value.current_step
        if (step !== 'waiting_for_answers' && step !== 'end' && step !== 'completed') {
          pollingTimer = setTimeout(pollExecutionStatus, currentPollInterval)
        }
      }
    } finally {
      sseReaderActive = false
      sseAbortController = null
    }
  }

  // ============================================================================
  // 实时同步（WebSocket）：接入统一订阅入口
  // - task 频道：以 thread_id 作为任务标识
  // - session 频道：工作流若关联 chat session，同样订阅
  // - 订阅在 viewTask / startWorkflow / onActivated 中触发；在 onDeactivated /
  //   onUnmounted / deleteTask / 切换任务 中清理
  // - SSE 仍保留用于当前请求的流式输出，轮询作为 WS 断连兜底，三者互不干扰
  // ============================================================================

  const clearRealtimeSubscriptions = () => {
    realtimeUnsubscribers.value.forEach(fn => {
      try { fn() } catch (e) { logger.warn('[WorkflowView] 取消订阅失败', e) }
    })
    realtimeUnsubscribers.value = []
    subscribedTaskId = null
    subscribedSessionId = null
  }

  /**
   * 为指定工作流执行订阅 WebSocket 实时事件
   * 幂等：若 thread_id 与 session_id 均与当前已订阅一致，则跳过
   * @param {{ thread_id?: string, chat_session_id?: string, session_id?: string } | null} taskObj
   */
  const subscribeRealtimeForTask = (taskObj) => {
    if (!taskObj) return
    // WorkflowView 用 thread_id 作为任务标识（与 DeepResearchView 的 task_id 对应）
    const taskId = taskObj.thread_id
    const chatSessionId = taskObj.chat_session_id || taskObj.session_id

    // 幂等判断：task 和 session 均已订阅则跳过（fresh 刷新后重复调用场景）
    if (taskId && taskId === subscribedTaskId && chatSessionId === subscribedSessionId) {
      return
    }

    // 清理上一任务的订阅，避免泄漏
    clearRealtimeSubscriptions()
    subscribedTaskId = taskId || null
    subscribedSessionId = chatSessionId || null

    if (taskId) {
      const unsubTask = realtime.subscribeTask(taskId, syncStore.handleRealtimeEvent, { replayFromSeq: 0 })
      realtimeUnsubscribers.value.push(unsubTask)
      if (chatSessionId) {
        const unsubSession = realtime.subscribeSession(chatSessionId, syncStore.handleRealtimeEvent, { replayFromSeq: 0 })
        realtimeUnsubscribers.value.push(unsubSession)
        logger.info(`[WorkflowView] 订阅 task=${taskId} + session=${chatSessionId}`)
      } else {
        logger.info(`[WorkflowView] 订阅 task=${taskId}（无关联 chat session）`)
      }
    }
  }

  // ============================================================================
  // WebSocket 事件 → execution.value 同步（workflowStore 监听）
  // - workflowStore 由 sync.js 的 onWorkflowEvent 回调写入（源自 task 频道 4 个 workflow_* 事件）
  // - 本 composable watch store 变化后合并到 execution.value，实现跨浏览器同步
  // - 与 SSE 双路径幂等：状态字段以最新事件为准，重复更新结果一致
  // - 请求浏览器同时收到 SSE + WebSocket，SSE 已直接更新 execution.value，此处合并为冗余兜底
  // - 非请求浏览器仅收到 WebSocket，此处合并是唯一更新路径
  // ============================================================================

  /** 从 workflowStore 读取当前 execution.thread_id 对应的状态（响应式） */
  const workflowStateFromStore = computed(() => {
    if (!execution.value?.thread_id) return null
    return workflowStore.getWorkflowState(execution.value.thread_id)
  })

  watch(workflowStateFromStore, (newState, oldState) => {
    if (!newState || !execution.value) return

    // 检测关键字段变化，避免无意义的重复合并
    const stepChanged = newState.current_step !== oldState?.current_step
    const statusChanged = newState.status !== oldState?.status
    const quizChanged = newState.quiz !== oldState?.quiz
    const scoreChanged = newState.score !== oldState?.score
    const feedbackChanged = newState.feedback !== oldState?.feedback
    const planChanged = newState.learning_plan !== oldState?.learning_plan

    if (!stepChanged && !statusChanged && !quizChanged && !scoreChanged
        && !feedbackChanged && !planChanged) return

    // 合并 store 状态到 execution.value（仅合并 store 中存在的字段）
    const merged = { ...execution.value }
    if (newState.current_step) merged.current_step = newState.current_step
    if (newState.status) merged.status = newState.status
    if (newState.learning_plan) merged.learning_plan = newState.learning_plan
    if (newState.retrieved_docs) merged.retrieved_docs = newState.retrieved_docs
    if (newState.quiz) merged.quiz = newState.quiz
    if (newState.score !== undefined) merged.score = newState.score
    if (newState.feedback !== undefined) merged.feedback = newState.feedback
    if (newState.should_retry !== undefined) merged.should_retry = newState.should_retry
    execution.value = merged

    // 更新步骤消息（store 中独立维护 step_message 字段）
    if (newState.step_message !== undefined) {
      currentStepMessage.value = newState.step_message
    }

    // quiz 变化时初始化答题表单（避免重复初始化）
    if (quizChanged && newState.quiz) {
      initAnswersFromQuiz(newState.quiz)
    }

    // 终态处理：完成时加载文件，失败时显示错误
    if (statusChanged) {
      if (newState.status === 'completed') {
        nextTick(() => {
          if (fileBrowserRef.value) {
            fileBrowserRef.value.loadFiles()
          }
          autoLoadKeyFile()
        })
      } else if (newState.status === 'failed') {
        const errorMsg = newState.error_message || newState.error
        if (errorMsg) {
          ElMessage.error(errorMsg)
        }
      }
    }

    logger.info(
      `[WorkflowView] workflowStore 同步到 execution: taskId=${execution.value.thread_id}, ` +
      `step=${newState.current_step || 'unknown'}, status=${newState.status || 'unknown'}`
    )
  })

  // ============================================================================
  // 任务生命周期：启动 / 查看 / 删除 / 重置 / 返回列表
  // ============================================================================

  /** 启动工作流 */
  const startWorkflow = async () => {
    if (!workflowForm.query.trim()) {
      ElMessage.warning('请输入学习主题')
      return
    }

    isLoading.value = true
    execution.value = null
    resetAnswers()
    currentPollInterval = BASE_POLL_INTERVAL

    try {
      const response = await workflowAPI.start(workflowForm)
      const result = response.data.data || response.data
      execution.value = result
      showDetail.value = true
      ElMessage.success('工作流已启动')

      stopPolling()
      // 启动新工作流后立即订阅 WebSocket 实时事件，避免在用户切走再回来前丢失事件
      subscribeRealtimeForTask(execution.value)
      connectSSE(result.thread_id)
    } catch (error) {
      logger.error('启动工作流失败:', error)
      ElMessage.error('启动工作流失败，请稍后重试')
    } finally {
      isLoading.value = false
    }
  }

  /**
   * 查看历史任务：根据任务当前状态决定重连 SSE 还是直接刷新
   * @param {Object} selectedTask - 选中的历史任务
   */
  const viewTask = async (selectedTask) => {
    closeSSE()
    stopPolling()
    clearAutoLoad()

    execution.value = selectedTask
    showDetail.value = true
    // 接入统一 WebSocket 实时同步：入口先订阅一次（基于 selectedTask 当前已知字段）
    subscribeRealtimeForTask(selectedTask)
    if (selectedTask.quiz) {
      initAnswersFromQuiz(selectedTask.quiz)
    }

    const isActive = selectedTask.status === 'running'
      || selectedTask.current_step === 'planner'
      || selectedTask.current_step === 'retrieval'
      || selectedTask.current_step === 'quiz_generator'
      || selectedTask.current_step === 'grading'
      || selectedTask.current_step === 'feedback'

    if (isActive && selectedTask.thread_id) {
      connectSSE(selectedTask.thread_id)
    } else if (selectedTask.thread_id) {
      try {
        const resp = await workflowAPI.getState(selectedTask.thread_id)
        const fresh = resp.data?.data || resp.data
        if (fresh) {
          execution.value = { ...selectedTask, ...fresh }
          // fresh 可能补充 chat_session_id 字段，重新订阅（幂等：若已订阅同 task+session 则跳过）
          subscribeRealtimeForTask(execution.value)
          const freshActive = fresh.current_step
            && fresh.current_step !== 'waiting_for_answers'
            && fresh.current_step !== 'end'
            && fresh.current_step !== 'completed'
            && fresh.current_step !== 'failed'
          if (freshActive) {
            connectSSE(fresh.thread_id)
          } else {
            // 已完成任务，自动加载文件和资料
            nextTick(() => {
              if (fileBrowserRef.value) {
                fileBrowserRef.value.loadFiles()
              }
              autoLoadKeyFile()
            })
          }
          if (fresh.quiz) {
            initAnswersFromQuiz(fresh.quiz)
          }
        }
      } catch (e) {
        logger.warn('获取工作流最新状态失败:', e)
      }
    }
  }

  /** 删除当前任务：取消订阅、清理 store、清空 execution */
  const deleteTask = () => {
    // 取消 WebSocket 实时订阅，避免对已删除任务继续接收事件
    clearRealtimeSubscriptions()
    // 清理 workflowStore 中对应任务的状态，避免内存泄漏与残留状态干扰
    if (execution.value?.thread_id) {
      workflowStore.clearWorkflowState(execution.value.thread_id)
    }
    execution.value = null
    showDetail.value = false
    resetAnswers()
  }

  /** 重置工作流：回到开始表单，清理所有状态与订阅 */
  const resetWorkflow = () => {
    // 清理 workflowStore 中对应任务的状态
    if (execution.value?.thread_id) {
      workflowStore.clearWorkflowState(execution.value.thread_id)
    }
    execution.value = null
    resetAnswers()
    workflowForm.query = ''
    workflowForm.knowledge_base_ids = []
    showDetail.value = false
    clearAutoLoad()
    stopPolling()
    closeSSE()
  }

  /** 返回列表（仅隐藏详情卡片，不清理执行状态与订阅，与原行为一致） */
  const backToList = () => {
    showDetail.value = false
  }

  return {
    isLoading,
    showDetail,
    currentStepMessage,
    realtimeUnsubscribers,
    workflowStateFromStore,
    startWorkflow,
    pollExecutionStatus,
    stopPolling,
    connectSSE,
    closeSSE,
    handleSSEEvent,
    subscribeRealtimeForTask,
    clearRealtimeSubscriptions,
    viewTask,
    deleteTask,
    resetWorkflow,
    backToList,
  }
}
