import { ref, onUnmounted, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import { workflowAPI } from '@/api/workflow'
import { readSSEStream } from '@/utils/sse'
import { extractSSEError } from '@/utils/apiErrorHandler'
import { logger } from '@/utils/logger'
import { useModelStore } from '@/stores/model'
import { LearningStep, LearningTaskStatus } from '@/types'

/**
 * 学习工作流执行流 composable
 *
 * 职责（自 WorkflowView 执行流域迁移，SSE 连接生命周期/事件路由/错误恢复逐字保持）：
 *   - startWorkflow：校验表单 → 本地 stub 进入详情 → SSE 流式启动（事件驱动推进）
 *   - _runSSE / startStreamWithEvents / startRestartStream / connectSSE / closeSSE：
 *     SSE 连接生命周期（AbortController 中断 + readSSEStream 逐事件消费）
 *   - handleSSEEvent：workflow_step / workflow_state_update / workflow_completed /
 *     workflow_failed / stream_error / error 事件路由到 execution 状态更新
 *
 * 状态归属：
 *   - isLoading / showDetail / currentStepMessage 由本 composable 持有并返回
 *     （仅执行流读写，视图经解构使用，ref 解构不丢响应性）
 *   - execution / answersForm / workflowForm 由视图持有注入
 *     （轮询 composable 与本 composable 共同依赖 execution，视图持有以打破
 *      「execution composable 需 polling、polling 需要 execution」的初始化环）
 *
 * 实例安全：sseAbortController / sseReaderActive 为调用闭包内私有，
 * 每次调用返回全新独立状态，无模块级可变全局；onUnmounted 自动断开 SSE。
 *
 * @param {object} deps
 * @param {import('vue').Ref<object|null>} deps.execution - 工作流执行状态（SSE 事件推进）
 * @param {object} deps.answersForm - 答题表单（waiting 事件到达时初始化各题空答案）
 * @param {object} deps.workflowForm - 启动表单（query / knowledgeBaseIds / useWebSearch）
 * @param {import('vue').Ref<object>} deps.fileBrowserRef - FileBrowser 组件 ref
 *   （学习计划生成/等待答题/完成时刷新「生成的文件」列表）
 * @param {(task: object) => void} deps.onThreadIdAssigned - 首个 start 事件回填
 *   thread_id 后订阅 WebSocket 实时同步（原 subscribeRealtimeForTask）
 * @param {() => void} deps.onWorkflowFinished - 工作流完成时加载关键文件
 *   （原 autoLoadKeyFile）
 * @param {{ stopPolling: () => void, schedulePolling: () => void }} deps.polling -
 *   轮询 composable 入口（启动前清理旧轮询；SSE 断开后回退轮询）
 * @returns {{
 *   isLoading: import('vue').Ref<boolean>,
 *   showDetail: import('vue').Ref<boolean>,
 *   currentStepMessage: import('vue').Ref<string>,
 *   startWorkflow: () => Promise<void>,
 *   startRestartStream: (threadId: string) => Promise<void>,
 *   connectSSE: (threadId: string) => Promise<void>,
 *   closeSSE: () => void,
 * }}
 */
export function useWorkflowExecution({
  execution,
  answersForm,
  workflowForm,
  fileBrowserRef,
  onThreadIdAssigned,
  onWorkflowFinished,
  polling,
}) {
  const modelStore = useModelStore()

  // isLoading（startWorkflow）与视图侧 isContinuing（continuePractice）维持手动管理：
  // 二者与 SSE 流式发起时序强耦合（见 cq-11 盘点表），不迁 useApiTask
  const isLoading = ref(false)
  const showDetail = ref(false)
  const currentStepMessage = ref('')

  /** @type {AbortController | null} */
  let sseAbortController = null
  let sseReaderActive = false

  const startWorkflow = async () => {
    if (!workflowForm.query.trim()) {
      ElMessage.warning('请输入学习主题')
      return
    }

    isLoading.value = true
    // 立即进入详情页：先以本地 stub 展示（thread_id 由后端首个事件回填），
    // 步骤条/学习计划/题目由 SSE 事件实时推进，不再阻塞等待同步 start 完成
    showDetail.value = true
    execution.value = {
      threadId: '',
      currentStep: LearningStep.START,
      status: LearningTaskStatus.RUNNING,
      userQuestion: workflowForm.query,
    }
    currentStepMessage.value = '工作流启动中...'
    Object.keys(answersForm).forEach(key => delete answersForm[key])
    polling.stopPolling()
    closeSSE()

    // 组装启动配置：模型/深度思考/网络查询以 modelStore 当前值为准
    // （ModelSelector 仅在手动切换模型时 emit change，未切换时表单字段为空，
    //  深度思考开关改动实时写入 modelStore.specialParams，需取最新值而非表单快照；
    //  useDeepThinking computed 为 modelStore.thinkingEnabled 纯透传，此处直接读等价）
    const payload = {
      ...workflowForm,
      providerId: modelStore.currentProviderId,
      modelName: modelStore.currentModelName,
      specialParams: modelStore.specialParams,
      useDeepThinking: modelStore.thinkingEnabled,
    }

    try {
      await startStreamWithEvents(payload)
    } catch (error) {
      logger.error('启动工作流失败:', error)
      const msg = error.response?.data?.message || error.message || '启动工作流失败，请稍后重试'
      ElMessage.error(msg)
      if (execution.value?.threadId) {
        execution.value.status = LearningTaskStatus.FAILED
      } else {
        execution.value = null
        showDetail.value = false
      }
    } finally {
      isLoading.value = false
    }
  }

  /**
   * 通用 SSE 流式执行：建立 fetch 流并逐事件交给 handleSSEEvent 驱动 execution
   * @param {() => Promise<Response>} requestFn 发起 SSE 请求的函数（不传 AbortSignal，
   *   避免 Vite proxy 对带 signal 的 SSE 响应整体缓冲；中断由 readSSEStream 的 signal 控制）
   */
  const _runSSE = async (requestFn) => {
    sseAbortController = new AbortController()
    sseReaderActive = true

    try {
      const response = await requestFn()

      if (!response.ok) {
        // rd-06：错误响应解析统一到 apiErrorHandler.extractSSEError
        throw await extractSSEError(response)
      }

      await readSSEStream(response, (data) => {
        if (!sseReaderActive) return
        handleSSEEvent(data)
      }, sseAbortController.signal)

      // 流正常结束（waiting_for_answers 中断时 handleSSEEvent 已 closeSSE，此处兜底关闭）
      if (sseReaderActive) closeSSE()
    } catch (error) {
      if (error.name === 'AbortError') {
        return
      }
      throw error
    } finally {
      sseReaderActive = false
      sseAbortController = null
    }
  }

  /** SSE 流式启动：请求内逐步执行工作流，事件由 handleSSEEvent 统一驱动 execution 更新 */
  const startStreamWithEvents = (payload) => _runSSE(
    () => workflowAPI.startStreamRaw(payload)
  )

  /** SSE 流式继续练习：快速创建新线程，SSE 逐步生成新一轮题目（与启动一致体验，不阻塞浏览器） */
  const startRestartStream = (threadId) => _runSSE(
    () => workflowAPI.restartStreamRaw(threadId)
  )

  const connectSSE = async (threadId) => {
    closeSSE()

    sseAbortController = new AbortController()
    sseReaderActive = true

    try {
      // streamFetchRaw：无 signal 直连，避免 Vite proxy 缓冲（token 走 query 参数）
      const response = await workflowAPI.streamFetchRaw(threadId)

      if (!response.ok) {
        // rd-06：错误响应解析统一到 apiErrorHandler.extractSSEError
        throw await extractSSEError(response)
      }

      await readSSEStream(response, (data) => {
        if (!sseReaderActive) return
        handleSSEEvent(data)
      }, sseAbortController.signal)

      if (sseReaderActive && execution.value) {
        const step = execution.value.currentStep
        if (step !== LearningStep.WAITING_FOR_ANSWERS && step !== LearningStep.END && step !== LearningStep.COMPLETED) {
          polling.schedulePolling()
        }
      }
    } catch (error) {
      if (error.name === 'AbortError') {
        return
      }
      logger.error('SSE连接失败，回退到轮询:', error)
      if (execution.value) {
        const step = execution.value.currentStep
        if (step !== LearningStep.WAITING_FOR_ANSWERS && step !== LearningStep.END && step !== LearningStep.COMPLETED) {
          polling.schedulePolling()
        }
      }
    } finally {
      sseReaderActive = false
      sseAbortController = null
    }
  }

  const handleSSEEvent = (sseData) => {
    // 命名边界：readSSEStream 已统一调用 parseProtocolEvent 完成转换
    // （toCamelCase 递归转换含 data 嵌套 + type 还原为后端原始 snake_case
    // 协议路由标识符），消费方只拿已转换对象。

    // SSE 事件格式：{ type: 'workflow_*', data: { ... } }
    // 与 task WebSocket 频道的 workflow_* 事件类型对齐，便于双路径统一处理
    switch (sseData.type) {
      case 'workflow_step':
        // 学习工作流节点执行进度（原 'start' 和 'step' 合并）
        // 新格式：{ type: 'workflow_step', data: { step, message, thread_id? } }
        currentStepMessage.value = sseData.data?.message || '工作流启动中...'
        if (execution.value && sseData.data?.step) {
          // 流式启动：首个 start 事件携带后端生成的 thread_id，回填 execution 并订阅实时同步
          if (sseData.data.threadId && !execution.value.threadId) {
            execution.value.threadId = sseData.data.threadId
            onThreadIdAssigned(execution.value)
          }
          // step 为后端 snake_case 协议值（如 waiting_for_answers），
          // 与 LearningStep 常量一致，保持原值不做键名式转换
          execution.value.currentStep = sseData.data.step
        }
        break
      case 'workflow_state_update':
        // 学习工作流状态变更
        if (execution.value && sseData.data) {
          const d = sseData.data
          if (d.currentStep) execution.value.currentStep = d.currentStep
          if (d.learningPlan) {
            const hadPlan = !!execution.value.learningPlan
            execution.value.learningPlan = d.learningPlan
            if (!hadPlan) {
              // 规划完成：learning_plan.md 已生成，自动刷新「生成的文件」，无需手动点击刷新
              nextTick(() => { fileBrowserRef.value?.loadFiles?.() })
            }
          }
          if (d.retrievedDocs) execution.value.retrievedDocs = d.retrievedDocs
          if (d.quiz) execution.value.quiz = d.quiz
          if (d.score !== undefined) execution.value.score = d.score
          if (d.feedback !== undefined) execution.value.feedback = d.feedback
          if (d.shouldRetry !== undefined) execution.value.shouldRetry = d.shouldRetry
          // waiting_for_answers 状态：原 'waiting' 行为，关闭 SSE 并初始化答题表单
          const isWaiting = d.state === LearningStep.WAITING_FOR_ANSWERS
            || d.currentStep === LearningStep.WAITING_FOR_ANSWERS
            || d.status === LearningTaskStatus.WAITING_FOR_ANSWERS
          if (isWaiting) {
            execution.value = { ...execution.value, ...d }
            currentStepMessage.value = d.message || '等待您提交答案...'
            if (d.quiz && !Object.keys(answersForm).length) {
              d.quiz.questions.forEach(q => {
                answersForm[q.id] = ''
              })
            }
            closeSSE()
            // 等待答题中断后，后端才执行状态持久化（learning_plan.md 落盘），延时刷新文件列表
            setTimeout(() => { fileBrowserRef.value?.loadFiles?.() }, 800)
          }
        }
        break
      case 'workflow_completed':
        // 学习工作流完成（原 'complete'）
        closeSSE()
        // 完成态不再展示中间步骤消息（避免「工作流启动中...」残留）
        currentStepMessage.value = ''
        if (execution.value && sseData.data) {
          execution.value = { ...execution.value, status: LearningTaskStatus.COMPLETED, ...sseData.data }
        }
        onWorkflowFinished()
        // 完成后自动刷新文件列表（report.md 等已生成）
        nextTick(() => { fileBrowserRef.value?.loadFiles?.() })
        break
      case 'workflow_failed':
        // 学习工作流失败（新增）
        closeSSE()
        if (execution.value && sseData.data) {
          execution.value = { ...execution.value, status: LearningTaskStatus.FAILED, ...sseData.data }
        }
        ElMessage.error(sseData.data?.error || sseData.data?.message || '工作流执行失败')
        break
      case 'stream_error':
        logger.warn('工作流流式执行异常:', sseData.message || sseData.data?.message)
        break
      case 'error':
        ElMessage.error(sseData.message || sseData.data?.message || '工作流执行出错')
        closeSSE()
        break
    }
  }

  const closeSSE = () => {
    sseReaderActive = false
    if (sseAbortController) {
      sseAbortController.abort()
      sseAbortController = null
    }
  }

  // 实例卸载自动断开 SSE（abort 中断 readSSEStream；keep-alive 失活由视图
  // onDeactivated 显式调用 closeSSE，语义与原卸载清理一致）
  onUnmounted(closeSSE)

  return {
    isLoading,
    showDetail,
    currentStepMessage,
    startWorkflow,
    startRestartStream,
    connectSSE,
    closeSSE,
  }
}
