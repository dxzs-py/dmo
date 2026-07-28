<template>
  <div class="workflow-view">
    <div class="view-content">
      <el-card v-if="!showDetail || !execution" class="start-card">
        <template #header>
          <div class="card-header">
            <span class="page-title">学习工作流</span>
          </div>
        </template>
        <el-form :model="workflowForm" label-width="100px">
          <el-form-item label="学习主题">
            <el-input
              v-model="workflowForm.query"
              type="textarea"
              :rows="3"
              placeholder="请输入您想学习的主题..."
            />
          </el-form-item>
          <el-form-item label="知识库">
            <KnowledgeBaseSelector v-model="workflowForm.knowledge_base_ids" />
            <div class="kb-tip">不选择知识库时将使用 AI 内置知识生成学习内容</div>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="isLoading" @click="startWorkflow">
              启动工作流
            </el-button>
          </el-form-item>
        </el-form>
      </el-card>

      <el-card v-if="showDetail && execution" class="detail-card">
        <template #header>
          <div class="card-header">
            <span>工作流详情</span>
            <div class="header-actions">
              <el-tag v-if="currentStepMessage" type="info" class="step-message">
                {{ currentStepMessage }}
              </el-tag>
              <el-tag :type="getStepType(execution.current_step)">
                {{ getStepText(execution.current_step) }}
              </el-tag>
              <el-button link type="primary" size="small" @click="showDetail = false">
                返回列表
              </el-button>
            </div>
          </div>
        </template>

        <div class="workflow-progress">
          <AiCanvas class="progress-canvas">
            <div class="progress-steps">
              <template v-for="(step, idx) in workflowSteps" :key="step.key">
                <div
                  :class="['progress-step', {
                    'is-completed': completedSteps.includes(step.key),
                    'is-active': execution?.current_step === step.key,
                    'is-pending': !completedSteps.includes(step.key) && execution?.current_step !== step.key
                  }]"
                >
                  <AiCheckpoint v-if="completedSteps.includes(step.key)" class="step-checkpoint">
                    <span class="step-label">{{ step.label }}</span>
                  </AiCheckpoint>
                  <div v-else class="step-node-wrapper">
                    <AiNode :title="step.label" :status="execution?.current_step === step.key ? 'running' : ''" />
                  </div>
                </div>
                <AiEdge
                  v-if="idx < workflowSteps.length - 1"
                  :status="completedSteps.includes(step.key) ? 'success' : (execution?.current_step === step.key ? 'active' : 'default')"
                />
              </template>
            </div>
          </AiCanvas>
        </div>

        <el-card v-if="execution.learning_plan" class="plan-card">
          <template #header>
            <div class="card-header">
              <span>📚 学习计划</span>
              <el-tag type="info">{{ execution.learning_plan.difficulty }}</el-tag>
            </div>
          </template>

          <h4>{{ execution.learning_plan.topic }}</h4>

          <div class="plan-section">
            <h5>🎯 学习目标</h5>
            <ul>
              <li v-for="(obj, idx) in execution.learning_plan.objectives" :key="idx">{{ obj }}</li>
            </ul>
          </div>

          <div class="plan-section">
            <h5>💡 关键知识点</h5>
            <ul>
              <li v-for="(point, idx) in execution.learning_plan.key_points" :key="idx">{{ point }}</li>
            </ul>
          </div>

          <div class="plan-info">
            <el-tag>预计时间: {{ execution.learning_plan.estimated_time }} 分钟</el-tag>
          </div>
        </el-card>

        <el-card v-if="execution.quiz" class="quiz-card">
          <template #header>
            <div class="card-header">
              <span>📝 练习题</span>
              <el-tag type="warning">等待答题</el-tag>
            </div>
          </template>

          <el-form :model="answersForm" label-width="0">
            <div v-for="question in execution.quiz.questions" :key="question.id" class="question-item">
              <div class="question-header">
                <span class="question-title">第 {{ question.id.replace('q', '') }} 题 ({{ question.points }} 分)</span>
                <el-tag size="small">{{ getQuestionTypeText(question.type) }}</el-tag>
              </div>
              <p class="question-text">{{ question.question }}</p>

              <div v-if="question.type === 'multiple_choice'" class="options">
                <el-radio-group v-model="answersForm[question.id]">
                  <el-radio v-for="(opt, idx) in question.options" :key="idx" :value="opt">
                    {{ String.fromCharCode(65 + idx) }}. {{ opt }}
                  </el-radio>
                </el-radio-group>
              </div>

              <el-input v-else-if="question.type === 'fill_blank'" v-model="answersForm[question.id]" placeholder="请填入答案" />

              <el-input v-else v-model="answersForm[question.id]" type="textarea" :rows="3" placeholder="请输入答案" />
            </div>

            <el-button type="primary" :loading="isSubmitting" size="large" @click="submitAnswers">
              提交答案
            </el-button>
          </el-form>
        </el-card>

        <el-card v-if="execution.score !== null" class="result-card">
          <template #header>
            <div class="card-header">
              <span>🎓 测验结果</span>
              <el-tag :type="execution.score >= 60 ? 'success' : 'danger'">
                {{ execution.score }} 分
              </el-tag>
            </div>
          </template>

          <div v-if="execution.feedback" class="feedback">
            <h5>📋 反馈</h5>
            <p>{{ execution.feedback }}</p>
          </div>

          <div v-if="execution.should_retry" class="retry-notice">
            <el-alert type="warning" title="未通过测验，将重新生成练习题..." show-icon />
          </div>

          <el-button v-if="!execution.should_retry" type="primary" @click="resetWorkflow">
            重新开始
          </el-button>
        </el-card>

        <el-card class="status-card">
          <template #header>
            <div class="card-header">
              <span>状态信息</span>
            </div>
          </template>

          <el-descriptions :column="1" border size="small">
            <el-descriptions-item label="线程ID">{{ execution.thread_id }}</el-descriptions-item>
            <el-descriptions-item label="查询">{{ execution.user_question }}</el-descriptions-item>
            <el-descriptions-item label="创建时间">{{ formatDate(execution.created_at) }}</el-descriptions-item>
            <el-descriptions-item label="更新时间">{{ formatDate(execution.updated_at) }}</el-descriptions-item>
          </el-descriptions>
        </el-card>

        <el-divider />

        <div v-if="autoLoadLoading" class="auto-load-section">
          <el-card>
            <div class="auto-load-loading">
              <el-icon class="is-loading" :size="16"><Loading /></el-icon>
              <span>正在加载学习资料...</span>
            </div>
          </el-card>
        </div>

        <div v-else-if="autoLoadContent" class="auto-load-section">
          <el-card>
            <template #header>
              <div class="card-header">
                <span>学习笔记</span>
              </div>
            </template>
            <MarkdownRenderer :content="autoLoadContent" />
          </el-card>
        </div>

        <div class="files-section">
          <h4>生成的文件</h4>
          <FileBrowser
            ref="fileBrowserRef"
            :task-id="execution.thread_id"
            :api="workflowAPI"
          />
        </div>
      </el-card>

      <el-card v-else class="task-list-card">
        <template #header>
          <div class="card-header">
            <span>历史任务</span>
          </div>
        </template>
        <TaskList
          ref="taskListRef"
          module-type="workflow"
          :api="workflowAPI"
          :status-options="statusOptions"
          @view-task="viewTask"
          @delete-task="deleteTask"
        />
      </el-card>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, onUnmounted, onActivated, onDeactivated, nextTick } from 'vue'
import { workflowAPI } from '../api'
import { readSSEStream } from '../utils/sse'
import { ElMessage } from 'element-plus'
import { Loading } from '@element-plus/icons-vue'
import TaskList from '../components/chat/TaskList.vue'
import FileBrowser from '../components/chat/FileBrowser.vue'
import MarkdownRenderer from '../components/common/MarkdownRenderer.vue'
import KnowledgeBaseSelector from '../components/common/KnowledgeBaseSelector.vue'
import AiCheckpoint from '../components/ai-elements/AiCheckpoint.vue'
import AiNode from '../components/ai-elements/AiNode.vue'
import AiConnection from '../components/ai-elements/AiConnection.vue'
import AiEdge from '../components/ai-elements/AiEdge.vue'
import AiCanvas from '../components/ai-elements/AiCanvas.vue'
import { formatDate } from '../utils/format'
import { logger } from '../utils/logger'
import { useRealtimeSync } from '@/composables/useRealtimeSync'
import { useSyncStore } from '@/stores/sync'
import { useWorkflowStore } from '@/stores/workflow'

const realtime = useRealtimeSync()
const syncStore = useSyncStore()
const workflowStore = useWorkflowStore()

const isLoading = ref(false)
const isSubmitting = ref(false)
const execution = ref(null)
const showDetail = ref(false)
const answersForm = reactive({})
const fileBrowserRef = ref(null)
const taskListRef = ref(null)
const currentStepMessage = ref('')
const autoLoadContent = ref(null)
const autoLoadLoading = ref(false)
let pollingTimer = null
let sseAbortController = null
let sseReaderActive = false
let currentPollInterval = 3000
const BASE_POLL_INTERVAL = 3000
const MAX_POLL_INTERVAL = 30000
const POLL_BACKOFF_FACTOR = 1.5

const workflowForm = reactive({
  query: '',
  knowledge_base_ids: [],
})

const statusOptions = [
  { value: 'running', label: '执行中' },
  { value: 'waiting_for_answers', label: '等待答题' },
  { value: 'retry', label: '重试' },
  { value: 'completed', label: '已完成' },
  { value: 'failed', label: '失败' },
]

const workflowSteps = [
  { key: 'start', label: '启动' },
  { key: 'planner', label: '规划' },
  { key: 'retrieval', label: '检索' },
  { key: 'quiz_generator', label: '出题' },
  { key: 'waiting_for_answers', label: '答题' },
  { key: 'grading', label: '评分' },
  { key: 'feedback', label: '反馈' },
  { key: 'end', label: '完成' },
]

const stepOrder = workflowSteps.map(s => s.key)

const completedSteps = computed(() => {
  if (!execution.value) return []
  const currentIdx = stepOrder.indexOf(execution.value.current_step)
  if (currentIdx < 0) return []
  return stepOrder.slice(0, currentIdx)
})

const getStepType = (step) => {
  const typeMap = {
    start: 'info',
    planner: 'primary',
    retrieval: 'primary',
    quiz_generator: 'warning',
    waiting_for_answers: 'warning',
    grading: 'primary',
    feedback: 'success',
    feedback_completed: 'success',
    end: 'success',
    completed: 'success',
  }
  return typeMap[step] || 'info'
}

const getStepText = (step) => {
  const textMap = {
    start: '准备中',
    planner: '生成学习计划',
    retrieval: '检索资料',
    quiz_generator: '生成练习题',
    waiting_for_answers: '等待答题',
    grading: '评分中',
    feedback: '生成反馈',
    feedback_completed: '工作流已完成',
    end: '已结束',
    completed: '已完成',
  }
  return textMap[step] || step
}

const getQuestionTypeText = (type) => {
  const map = {
    multiple_choice: '选择题',
    fill_blank: '填空题',
    short_answer: '简答题',
  }
  return map[type] || type
}

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
    if (responseData.quiz && !Object.keys(answersForm).length) {
      responseData.quiz.questions.forEach(q => {
        answersForm[q.id] = ''
      })
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

const stopPolling = () => {
  if (pollingTimer) {
    clearTimeout(pollingTimer)
    pollingTimer = null
  }
  currentPollInterval = BASE_POLL_INTERVAL
}

const startWorkflow = async () => {
  if (!workflowForm.query.trim()) {
    ElMessage.warning('请输入学习主题')
    return
  }

  isLoading.value = true
  execution.value = null
  Object.keys(answersForm).forEach(key => delete answersForm[key])
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
      } catch {}
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

const handleSSEEvent = (data) => {
  // SSE 事件格式：{ type: 'workflow_*', data: { ... } }
  // 与 task WebSocket 频道的 workflow_* 事件类型对齐，便于双路径统一处理
  switch (data.type) {
    case 'workflow_step':
      // 学习工作流节点执行进度（原 'start' 和 'step' 合并）
      // 新格式：{ type: 'workflow_step', data: { step, message } }
      currentStepMessage.value = data.data?.message || '工作流启动中...'
      if (execution.value && data.data?.step) {
        execution.value.current_step = data.data.step
      }
      break
    case 'workflow_state_update':
      // 学习工作流状态变更（原 'state_update' 和 'waiting' 合并）
      // 新格式：{ type: 'workflow_state_update', data: { current_step, learning_plan, quiz, state, ... } }
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
          if (data.data.quiz && !Object.keys(answersForm).length) {
            data.data.quiz.questions.forEach(q => {
              answersForm[q.id] = ''
            })
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

const closeSSE = () => {
  sseReaderActive = false
  if (sseAbortController) {
    sseAbortController.abort()
    sseAbortController = null
  }
}

// ============================================================================
// 实时同步（WebSocket）：接入统一订阅入口
// - task 频道：以 thread_id 作为任务标识，订阅工具调用 / 状态更新等事件
// - session 频道：工作流若关联 chat session（未来扩展），同样订阅
// - 订阅在 viewTask 入口与 onActivated 中触发；在 onDeactivated/onUnmounted/deleteTask 中清理
// - SSE 仍保留用于当前请求的流式输出，轮询作为 WS 断连兜底，二者互不干扰
// ============================================================================
/** @type {import('vue').Ref<Array<() => void>>} */
const realtimeUnsubscribers = ref([])
let subscribedTaskId = null
let subscribedSessionId = null

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
// - WorkflowView watch store 变化后合并到 execution.value，实现跨浏览器同步
// - 与 SSE 双路径幂等：状态字段以最新事件为准，重复更新结果一致
// - 请求浏览器同时收到 SSE + WebSocket，SSE 已直接更新 execution.value，此处合并为冗余兜底
// - 非请求浏览器仅收到 WebSocket，此处合并是唯一更新路径
// ============================================================================
/**
 * 从 workflowStore 读取当前 execution.thread_id 对应的状态（响应式）
 * @type {import('vue').ComputedRef<Object|null>}
 */
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
  if (quizChanged && newState.quiz && !Object.keys(answersForm).length) {
    newState.quiz.questions.forEach(q => {
      answersForm[q.id] = ''
    })
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

const submitAnswers = async () => {
  const hasEmpty = execution.value.quiz.questions.some(q => !answersForm[q.id])
  if (hasEmpty) {
    ElMessage.warning('请完成所有题目')
    return
  }

  isSubmitting.value = true

  try {
    const response = await workflowAPI.submitAnswers(execution.value.thread_id, answersForm)
    const responseData = response.data.data || response.data
    execution.value = { ...execution.value, ...responseData }
    ElMessage.success('答案已提交')

    if (responseData.should_retry) {
      Object.keys(answersForm).forEach(key => delete answersForm[key])
      stopPolling()
      connectSSE(execution.value.thread_id)
    } else {
      nextTick(() => {
        if (fileBrowserRef.value) {
          fileBrowserRef.value.loadFiles()
        }
        autoLoadKeyFile()
      })
    }
  } catch (error) {
    logger.error('提交答案失败:', error)
    const msg = error.response?.data?.message || error.message || '提交答案失败，请稍后重试'
    ElMessage.error(msg)
  } finally {
    isSubmitting.value = false
  }
}

const resetWorkflow = () => {
  // 清理 workflowStore 中对应任务的状态
  if (execution.value?.thread_id) {
    workflowStore.clearWorkflowState(execution.value.thread_id)
  }
  execution.value = null
  Object.keys(answersForm).forEach(key => delete answersForm[key])
  workflowForm.query = ''
  workflowForm.knowledge_base_ids = []
  showDetail.value = false
  autoLoadContent.value = null
  stopPolling()
  closeSSE()
}

/** 自动查找并加载学习工作流生成的关键文件 */
const _findKeyFile = async () => {
  if (!execution.value?.thread_id) return null
  try {
    const res = await workflowAPI.getFiles(execution.value.thread_id)
    const data = res.data?.data || res.data
    const files = data?.files || data || []
    // 优先查找 notes/ 目录下的 .md 文件
    const notesDir = files.find(f => f.name === 'notes' && f.type === 'directory')
    if (notesDir && notesDir.children) {
      const mdFile = notesDir.children.find(f => f.name?.endsWith('.md'))
      if (mdFile) return `notes/${mdFile.name}`
      const txtFile = notesDir.children.find(f => f.name?.endsWith('.txt'))
      if (txtFile) return `notes/${txtFile.name}`
    }
    // 查找根目录下非 report 的 .md 文件
    const mdFiles = files.filter(f => f.type === 'file' && f.name?.endsWith('.md') && !f.name?.includes('report'))
    if (mdFiles.length > 0) return mdFiles[0].relative_path || mdFiles[0].name
    // 查找根目录下的 .txt 文件
    const rootNotes = files.filter(f => f.type === 'file' && f.name?.endsWith('.txt'))
    if (rootNotes.length > 0) return rootNotes[0].relative_path || rootNotes[0].name
  } catch {}
  return null
}

const autoLoadKeyFile = async () => {
  if (!execution.value?.thread_id) return
  autoLoadContent.value = null
  autoLoadLoading.value = true
  try {
    const file = await _findKeyFile()
    if (file) {
      const response = await workflowAPI.getFileContent(execution.value.thread_id, file)
      const data = response.data?.data || response.data
      autoLoadContent.value = data?.content || data || ''
    }
  } catch (error) {
    logger.warn('自动加载学习资料失败:', error)
  } finally {
    autoLoadLoading.value = false
  }
}

const viewTask = async (selectedTask) => {
  closeSSE()
  stopPolling()
  autoLoadContent.value = null

  execution.value = selectedTask
  showDetail.value = true
  // 接入统一 WebSocket 实时同步：入口先订阅一次（基于 selectedTask 当前已知字段）
  subscribeRealtimeForTask(selectedTask)
  if (selectedTask.quiz && !Object.keys(answersForm).length) {
    selectedTask.quiz.questions.forEach(q => {
      answersForm[q.id] = ''
    })
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
        if (fresh.quiz && !Object.keys(answersForm).length) {
          fresh.quiz.questions.forEach(q => {
            answersForm[q.id] = ''
          })
        }
      }
    } catch (e) {
      logger.warn('获取工作流最新状态失败:', e)
    }
  }
}

const deleteTask = () => {
  // 取消 WebSocket 实时订阅，避免对已删除任务继续接收事件
  clearRealtimeSubscriptions()
  // 清理 workflowStore 中对应任务的状态，避免内存泄漏与残留状态干扰
  if (execution.value?.thread_id) {
    workflowStore.clearWorkflowState(execution.value.thread_id)
  }
  execution.value = null
  showDetail.value = false
  Object.keys(answersForm).forEach(key => delete answersForm[key])
}

// keep-alive 激活时：恢复 WebSocket 订阅（onDeactivated 时已清理）
// 首次挂载时 onActivated 也会触发，此时 execution.value 通常为 null，subscribeRealtimeForTask 会安全跳过
onActivated(() => {
  if (execution.value && execution.value.thread_id) {
    subscribeRealtimeForTask(execution.value)
  }
})

// keep-alive 停用时：清理 SSE、轮询与 WebSocket 订阅，避免后台资源浪费
onDeactivated(() => {
  stopPolling()
  closeSSE()
  clearRealtimeSubscriptions()
})

onUnmounted(() => {
  stopPolling()
  closeSSE()
  clearRealtimeSubscriptions()
})
</script>

<style scoped>
.workflow-view {
  height: 100%;
  padding: 24px;
  overflow-y: auto;
}

.view-content {
  max-width: 1200px;
  margin: 0 auto;
}

.page-title {
  font-size: 18px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.start-card,
.detail-card,
.task-list-card {
  margin-bottom: 20px;
}

.kb-tip {
  margin-top: 6px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.workflow-progress {
  margin-bottom: 20px;
  padding: 16px;
  background: var(--el-fill-color-lighter);
  border-radius: 8px;
  overflow-x: auto;
}

.progress-canvas {
  background: transparent;
  min-height: auto;
}

.progress-steps {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: max-content;
}

.progress-step {
  flex-shrink: 0;
}

.progress-step.is-completed .step-label {
  color: var(--el-color-success);
  font-weight: 600;
}

.progress-step.is-active .step-node-wrapper {
  animation: pulse-border 2s ease-in-out infinite;
}

.progress-step.is-pending {
  opacity: 0.5;
}

.step-checkpoint {
  padding: 6px 10px;
}

.step-label {
  font-size: 13px;
}

.step-node-wrapper :deep(.ai-node) {
  min-width: 80px;
}

@keyframes pulse-border {
  0%, 100% { box-shadow: 0 0 0 0 rgba(64, 158, 255, 0.3); }
  50% { box-shadow: 0 0 0 4px rgba(64, 158, 255, 0.1); }
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.step-message {
  animation: pulse-opacity 2s ease-in-out infinite;
}

@keyframes pulse-opacity {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.6; }
}

.plan-card,
.quiz-card,
.result-card,
.status-card {
  margin-bottom: 20px;
}

.plan-card h4 {
  margin: 0 0 16px 0;
  font-size: 18px;
}

.plan-section {
  margin-bottom: 16px;
}

.plan-section h5 {
  margin: 0 0 8px 0;
  font-size: 14px;
  color: var(--el-text-color-regular);
}

.plan-section ul {
  margin: 0;
  padding-left: 20px;
}

.plan-section li {
  margin-bottom: 6px;
}

.plan-info {
  margin-top: 16px;
}

.question-item {
  margin-bottom: 24px;
  padding-bottom: 24px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.question-item:last-child {
  border-bottom: none;
  margin-bottom: 0;
  padding-bottom: 0;
}

.question-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.question-title {
  font-weight: 600;
  font-size: 15px;
}

.question-text {
  margin: 0 0 16px 0;
  font-size: 15px;
  line-height: 1.6;
}

.options {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.feedback {
  margin-bottom: 16px;
}

.feedback h5 {
  margin: 0 0 8px 0;
  font-size: 14px;
}

.feedback p {
  margin: 0;
  padding: 12px;
  background: var(--el-fill-color-lighter);
  border-radius: 4px;
  line-height: 1.6;
}

.retry-notice {
  margin-bottom: 16px;
}

.files-section {
  margin-top: 8px;
}

.auto-load-section {
  margin-bottom: 20px;
}

.auto-load-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 20px;
  color: var(--el-text-color-secondary);
}

.files-section h4 {
  margin: 0 0 16px 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

@media (max-width: 1024px) {
  .workflow-view {
    padding: 16px;
  }

  .page-title {
    font-size: 16px;
  }
}

@media (max-width: 768px) {
  .workflow-view {
    padding: 12px;
  }

  .page-title {
    font-size: 15px;
  }

  .card-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }

  .header-actions {
    flex-wrap: wrap;
  }
}

@media (max-width: 480px) {
  .workflow-view {
    padding: 8px;
  }

  .page-title {
    font-size: 14px;
  }

  .workflow-progress {
    padding: 8px;
  }

  .question-title {
    font-size: 14px;
  }

  .question-text {
    font-size: 14px;
  }
}
</style>
