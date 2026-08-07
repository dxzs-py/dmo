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
            <KnowledgeBaseSelector v-model="workflowForm.knowledgeBaseIds" />
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
              <el-tag :type="getStepType(execution.currentStep)">
                {{ getStepText(execution.currentStep) }}
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
                    'is-active': execution?.currentStep === step.key,
                    'is-pending': !completedSteps.includes(step.key) && execution?.currentStep !== step.key
                  }]"
                >
                  <AiCheckpoint v-if="completedSteps.includes(step.key)" class="step-checkpoint">
                    <span class="step-label">{{ step.label }}</span>
                  </AiCheckpoint>
                  <div v-else class="step-node-wrapper">
                    <AiNode :title="step.label" :status="execution?.currentStep === step.key ? LearningTaskStatus.RUNNING : ''" />
                  </div>
                </div>
                <AiEdge
                  v-if="idx < workflowSteps.length - 1"
                  :status="completedSteps.includes(step.key) ? 'success' : (execution?.currentStep === step.key ? 'active' : 'default')"
                />
              </template>
            </div>
          </AiCanvas>
        </div>

        <el-card v-if="execution.learningPlan" class="plan-card">
          <template #header>
            <div class="card-header">
              <span>📚 学习计划</span>
              <el-tag type="info">{{ execution.learningPlan.difficulty }}</el-tag>
            </div>
          </template>

          <h4>{{ execution.learningPlan.topic }}</h4>

          <div class="plan-section">
            <h5>🎯 学习目标</h5>
            <ul>
              <li v-for="(obj, idx) in execution.learningPlan.objectives" :key="idx">{{ obj }}</li>
            </ul>
          </div>

          <div class="plan-section">
            <h5>💡 关键知识点</h5>
            <ul>
              <li v-for="(point, idx) in execution.learningPlan.keyPoints" :key="idx">{{ point }}</li>
            </ul>
          </div>

          <div class="plan-info">
            <el-tag>预计时间: {{ execution.learningPlan.estimatedTime }} 分钟</el-tag>
          </div>
        </el-card>

        <WorkflowQuiz
          :quiz="execution.quiz"
          :answers-form="answersForm"
          :is-submitting="isSubmitting"
          :score="execution.score"
          :feedback="execution.feedback"
          :should-retry="execution.shouldRetry"
          :get-question-type-text="getQuestionTypeText"
          @submit="submitAnswers"
          @reset="resetWorkflow"
        />

        <el-card class="status-card">
          <template #header>
            <div class="card-header">
              <span>状态信息</span>
            </div>
          </template>

          <el-descriptions :column="1" border size="small">
            <el-descriptions-item label="线程ID">{{ execution.threadId }}</el-descriptions-item>
            <el-descriptions-item label="查询">{{ execution.userQuestion }}</el-descriptions-item>
            <el-descriptions-item label="创建时间">{{ formatDate(execution.createdAt) }}</el-descriptions-item>
            <el-descriptions-item label="更新时间">{{ formatDate(execution.updatedAt) }}</el-descriptions-item>
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
            :task-id="execution.threadId"
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
import { workflowAPI } from '@/api/workflow'
import { readSSEStream } from '../utils/sse'
import { toCamelCase, convertSnakeToCamel } from '@/utils/sessionTransformers'
import { ElMessage } from 'element-plus'
import { Loading } from '@element-plus/icons-vue'
import TaskList from '../components/chat/TaskList.vue'
import FileBrowser from '../components/chat/FileBrowser.vue'
import WorkflowQuiz from '../components/workflow/WorkflowQuiz.vue'
import MarkdownRenderer from '../components/common/MarkdownRenderer.vue'
import KnowledgeBaseSelector from '../components/common/KnowledgeBaseSelector.vue'
import AiCheckpoint from '../components/ai-elements/AiCheckpoint.vue'
import AiNode from '../components/ai-elements/AiNode.vue'
import AiConnection from '../components/ai-elements/AiConnection.vue'
import AiEdge from '../components/ai-elements/AiEdge.vue'
import AiCanvas from '../components/ai-elements/AiCanvas.vue'
import { formatDate } from '../utils/format'
import { logger } from '../utils/logger'
import { useTaskRealtimeSync } from '@/composables/useTaskRealtimeSync'
import { useWorkflowStore } from '@/stores/workflow'
import { LearningStep, LearningTaskStatus } from '@/types'

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
  knowledgeBaseIds: [],
})

const statusOptions = [
  { value: LearningTaskStatus.RUNNING, label: '执行中' },
  { value: LearningTaskStatus.WAITING_FOR_ANSWERS, label: '等待答题' },
  { value: LearningTaskStatus.RETRY, label: '重试' },
  { value: LearningTaskStatus.COMPLETED, label: '已完成' },
  { value: LearningTaskStatus.FAILED, label: '失败' },
]

const workflowSteps = [
  { key: LearningStep.START, label: '启动' },
  { key: LearningStep.PLANNER, label: '规划' },
  { key: LearningStep.RETRIEVAL, label: '检索' },
  { key: LearningStep.QUIZ_GENERATOR, label: '出题' },
  { key: LearningStep.WAITING_FOR_ANSWERS, label: '答题' },
  { key: LearningStep.GRADING, label: '评分' },
  { key: LearningStep.FEEDBACK, label: '反馈' },
  { key: LearningStep.END, label: '完成' },
]

const stepOrder = workflowSteps.map(s => s.key)

const completedSteps = computed(() => {
  if (!execution.value) return []
  const currentIdx = stepOrder.indexOf(execution.value.currentStep)
  if (currentIdx < 0) return []
  return stepOrder.slice(0, currentIdx)
})

const getStepType = (step) => {
  const typeMap = {
    [LearningStep.START]: 'info',
    [LearningStep.PLANNER]: 'primary',
    [LearningStep.RETRIEVAL]: 'primary',
    [LearningStep.QUIZ_GENERATOR]: 'warning',
    [LearningStep.WAITING_FOR_ANSWERS]: 'warning',
    [LearningStep.GRADING]: 'primary',
    [LearningStep.FEEDBACK]: 'success',
    [LearningStep.FEEDBACK_COMPLETED]: 'success',
    [LearningStep.END]: 'success',
    [LearningTaskStatus.COMPLETED]: 'success',
  }
  return typeMap[step] || 'info'
}

const getStepText = (step) => {
  const textMap = {
    [LearningStep.START]: '准备中',
    [LearningStep.PLANNER]: '生成学习计划',
    [LearningStep.RETRIEVAL]: '检索资料',
    [LearningStep.QUIZ_GENERATOR]: '生成练习题',
    [LearningStep.WAITING_FOR_ANSWERS]: '等待答题',
    [LearningStep.GRADING]: '评分中',
    [LearningStep.FEEDBACK]: '生成反馈',
    [LearningStep.FEEDBACK_COMPLETED]: '工作流已完成',
    [LearningStep.END]: '已结束',
    [LearningTaskStatus.COMPLETED]: '已完成',
  }
  return textMap[step] || step
}

const getQuestionTypeText = (type) => {
  const map = {
    multipleChoice: '选择题',
    fillBlank: '填空题',
    shortAnswer: '简答题',
  }
  return map[type] || type
}

const pollExecutionStatus = async () => {
  if (!execution.value) {
    stopPolling()
    return
  }

  const currentStep = execution.value.currentStep

  if (currentStep === LearningStep.WAITING_FOR_ANSWERS || currentStep === LearningStep.END || currentStep === LearningStep.COMPLETED) {
    stopPolling()
    if (fileBrowserRef.value && currentStep !== LearningStep.WAITING_FOR_ANSWERS) {
      fileBrowserRef.value.loadFiles()
      autoLoadKeyFile()
    }
    return
  }

  try {
    const response = await workflowAPI.getState(execution.value.threadId)
    const data = response.data
    execution.value = { ...execution.value, ...(data.data || data) }

    const responseData = data.data || data
    if (responseData.quiz && !Object.keys(answersForm).length) {
      responseData.quiz.questions.forEach(q => {
        answersForm[q.id] = ''
      })
    }

    if (currentStep !== execution.value.currentStep) {
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
    connectSSE(result.threadId)
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
      const step = execution.value.currentStep
      if (step !== LearningStep.WAITING_FOR_ANSWERS && step !== LearningStep.END && step !== LearningStep.COMPLETED) {
        pollingTimer = setTimeout(pollExecutionStatus, currentPollInterval)
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
        pollingTimer = setTimeout(pollExecutionStatus, currentPollInterval)
      }
    }
  } finally {
    sseReaderActive = false
    sseAbortController = null
  }
}

const handleSSEEvent = (data) => {
  // 命名边界：统一解析——对 sseData 整体调用 toCamelCase 递归转换（含 data 嵌套），
  // 然后将 type 还原为后端原始 snake_case（协议路由标识符，非业务数据）。
  // 原散落的 data.data 局部转换（toCamelCase(data.data)）收敛于此一次完成。
  const sseData = toCamelCase(data)
  if (data && typeof data === 'object') {
    sseData.type = data.type
  }

  // SSE 事件格式：{ type: 'workflow_*', data: { ... } }
  // 与 task WebSocket 频道的 workflow_* 事件类型对齐，便于双路径统一处理
  switch (sseData.type) {
    case 'workflow_step':
      // 学习工作流节点执行进度（原 'start' 和 'step' 合并）
      // 新格式：{ type: 'workflow_step', data: { step, message } }
      currentStepMessage.value = sseData.data?.message || '工作流启动中...'
      if (execution.value && sseData.data?.step) {
        // step 值是后端 snake_case 字符串（如 waiting_for_answers），
        // 需单独转 camelCase：toCamelCase 仅转换键名、不转换字符串值
        execution.value.currentStep = convertSnakeToCamel(sseData.data.step)
      }
      break
    case 'workflow_state_update':
      // 学习工作流状态变更
      if (execution.value && sseData.data) {
        const d = sseData.data
        if (d.currentStep) execution.value.currentStep = d.currentStep
        if (d.learningPlan) execution.value.learningPlan = d.learningPlan
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
          if (d.quiz && !Object.keys(answersForm).length) {
            d.quiz.questions.forEach(q => {
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
      if (execution.value && sseData.data) {
        execution.value = { ...execution.value, status: LearningTaskStatus.COMPLETED, ...sseData.data }
      }
      if (fileBrowserRef.value) {
        fileBrowserRef.value.loadFiles()
      }
      autoLoadKeyFile()
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

const { subscribeRealtimeForTask, clearRealtimeSubscriptions } = useTaskRealtimeSync('Workflow', 'threadId')

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
  if (!execution.value?.threadId) return null
  return workflowStore.getWorkflowState(execution.value.threadId)
})

watch(workflowStateFromStore, (newState, oldState) => {
  if (!newState || !execution.value) return

  // 检测关键字段变化，避免无意义的重复合并
  const stepChanged = newState.currentStep !== oldState?.currentStep
  const statusChanged = newState.status !== oldState?.status
  const quizChanged = newState.quiz !== oldState?.quiz
  const scoreChanged = newState.score !== oldState?.score
  const feedbackChanged = newState.feedback !== oldState?.feedback
  const planChanged = newState.learningPlan !== oldState?.learningPlan

  if (!stepChanged && !statusChanged && !quizChanged && !scoreChanged
      && !feedbackChanged && !planChanged) return

  // 合并 store 状态到 execution.value（仅合并 store 中存在的字段）
  const merged = { ...execution.value }
  if (newState.currentStep) merged.currentStep = newState.currentStep
  if (newState.status) merged.status = newState.status
  if (newState.learningPlan) merged.learningPlan = newState.learningPlan
  if (newState.retrievedDocs) merged.retrievedDocs = newState.retrievedDocs
  if (newState.quiz) merged.quiz = newState.quiz
  if (newState.score !== undefined) merged.score = newState.score
  if (newState.feedback !== undefined) merged.feedback = newState.feedback
  if (newState.shouldRetry !== undefined) merged.shouldRetry = newState.shouldRetry
  execution.value = merged

  // 更新步骤消息（store 中独立维护 stepMessage 字段）
  if (newState.stepMessage !== undefined) {
    currentStepMessage.value = newState.stepMessage
  }

  // quiz 变化时初始化答题表单（避免重复初始化）
  if (quizChanged && newState.quiz && !Object.keys(answersForm).length) {
    newState.quiz.questions.forEach(q => {
      answersForm[q.id] = ''
    })
  }

  // 终态处理：完成时加载文件，失败时显示错误
  if (statusChanged) {
    if (newState.status === LearningTaskStatus.COMPLETED) {
      nextTick(() => {
        if (fileBrowserRef.value) {
          fileBrowserRef.value.loadFiles()
        }
        autoLoadKeyFile()
      })
    } else if (newState.status === LearningTaskStatus.FAILED) {
      const errorMsg = newState.errorMessage || newState.error
      if (errorMsg) {
        ElMessage.error(errorMsg)
      }
    }
  }

  logger.info(
    `[WorkflowView] workflowStore 同步到 execution: taskId=${execution.value.threadId}, ` +
    `step=${newState.currentStep || 'unknown'}, status=${newState.status || 'unknown'}`
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
    const response = await workflowAPI.submitAnswers(execution.value.threadId, answersForm)
    const responseData = response.data.data || response.data
    execution.value = { ...execution.value, ...responseData }
    ElMessage.success('答案已提交')

    if (responseData.shouldRetry) {
      Object.keys(answersForm).forEach(key => delete answersForm[key])
      stopPolling()
      connectSSE(execution.value.threadId)
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
  if (execution.value?.threadId) {
    workflowStore.clearWorkflowState(execution.value.threadId)
  }
  execution.value = null
  Object.keys(answersForm).forEach(key => delete answersForm[key])
  workflowForm.query = ''
  workflowForm.knowledgeBaseIds = []
  showDetail.value = false
  autoLoadContent.value = null
  stopPolling()
  closeSSE()
}

/** 自动查找并加载学习工作流生成的关键文件 */
const _findKeyFile = async () => {
  if (!execution.value?.threadId) return null
  try {
    const res = await workflowAPI.getFiles(execution.value.threadId)
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
    if (mdFiles.length > 0) return mdFiles[0].relativePath || mdFiles[0].name
    // 查找根目录下的 .txt 文件
    const rootNotes = files.filter(f => f.type === 'file' && f.name?.endsWith('.txt'))
    if (rootNotes.length > 0) return rootNotes[0].relativePath || rootNotes[0].name
  } catch {}
  return null
}

const autoLoadKeyFile = async () => {
  if (!execution.value?.threadId) return
  autoLoadContent.value = null
  autoLoadLoading.value = true
  try {
    const file = await _findKeyFile()
    if (file) {
      const response = await workflowAPI.getFileContent(execution.value.threadId, file)
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

  const isActive = selectedTask.status === LearningTaskStatus.RUNNING
    || selectedTask.currentStep === LearningStep.PLANNER
    || selectedTask.currentStep === LearningStep.RETRIEVAL
    || selectedTask.currentStep === LearningStep.QUIZ_GENERATOR
    || selectedTask.currentStep === LearningStep.GRADING
    || selectedTask.currentStep === LearningStep.FEEDBACK

  if (isActive && selectedTask.threadId) {
    connectSSE(selectedTask.threadId)
  } else if (selectedTask.threadId) {
    try {
      const resp = await workflowAPI.getState(selectedTask.threadId)
      const fresh = resp.data?.data || resp.data
      if (fresh) {
        execution.value = { ...selectedTask, ...fresh }
        // fresh 可能补充 chat_session_id 字段，重新订阅（幂等：若已订阅同 task+session 则跳过）
        subscribeRealtimeForTask(execution.value)
        const freshActive = fresh.currentStep
          && fresh.currentStep !== LearningStep.WAITING_FOR_ANSWERS
          && fresh.currentStep !== LearningStep.END
          && fresh.currentStep !== LearningStep.COMPLETED
          && fresh.currentStep !== LearningStep.FAILED
        if (freshActive) {
          connectSSE(fresh.threadId)
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
  if (execution.value?.threadId) {
    workflowStore.clearWorkflowState(execution.value.threadId)
  }
  execution.value = null
  showDetail.value = false
  Object.keys(answersForm).forEach(key => delete answersForm[key])
}

// keep-alive 激活时：恢复 WebSocket 订阅（onDeactivated 时已清理）
// 首次挂载时 onActivated 也会触发，此时 execution.value 通常为 null，subscribeRealtimeForTask 会安全跳过
onActivated(() => {
  if (execution.value && execution.value.threadId) {
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
