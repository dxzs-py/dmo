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
import { ref, reactive, computed, onUnmounted } from 'vue'
import { workflowAPI } from '../api'
import { readSSEStream } from '../utils/sse'
import { ElMessage } from 'element-plus'
import TaskList from '../components/chat/TaskList.vue'
import FileBrowser from '../components/chat/FileBrowser.vue'
import AiCheckpoint from '../components/ai-elements/AiCheckpoint.vue'
import AiNode from '../components/ai-elements/AiNode.vue'
import AiConnection from '../components/ai-elements/AiConnection.vue'
import AiEdge from '../components/ai-elements/AiEdge.vue'
import AiCanvas from '../components/ai-elements/AiCanvas.vue'
import { formatDate as _formatDate } from '../utils/format'
import { logger } from '../utils/logger'

const isLoading = ref(false)
const isSubmitting = ref(false)
const execution = ref(null)
const showDetail = ref(false)
const answersForm = reactive({})
const fileBrowserRef = ref(null)
const currentStepMessage = ref('')
let pollingTimer = null
let sseAbortController = null
let sseReaderActive = false
let currentPollInterval = 3000
const BASE_POLL_INTERVAL = 3000
const MAX_POLL_INTERVAL = 30000
const POLL_BACKOFF_FACTOR = 1.5

const workflowForm = reactive({
  query: '',
})

const statusOptions = [
  { value: 'start', label: '准备中' },
  { value: 'planner', label: '生成学习计划' },
  { value: 'retrieval', label: '检索资料' },
  { value: 'quiz_generator', label: '生成练习题' },
  { value: 'waiting_for_answers', label: '等待答题' },
  { value: 'grading', label: '评分中' },
  { value: 'feedback', label: '生成反馈' },
  { value: 'end', label: '已结束' },
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

const formatDate = (dateStr) => {
  if (!dateStr) return '-'
  return _formatDate(dateStr)
}

const getStepType = (step) => {
  const typeMap = {
    start: 'info',
    planner: 'primary',
    retrieval: 'primary',
    quiz_generator: 'warning',
    waiting_for_answers: 'warning',
    grading: 'primary',
    feedback: 'success',
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
  switch (data.type) {
    case 'start':
      currentStepMessage.value = data.message || '工作流启动中...'
      break
    case 'step':
      currentStepMessage.value = data.message || ''
      if (execution.value && data.step) {
        execution.value.current_step = data.step
      }
      break
    case 'state_update':
      if (execution.value && data.data) {
        if (data.data.current_step) execution.value.current_step = data.data.current_step
        if (data.data.learning_plan) execution.value.learning_plan = data.data.learning_plan
        if (data.data.retrieved_docs) execution.value.retrieved_docs = data.data.retrieved_docs
        if (data.data.quiz) execution.value.quiz = data.data.quiz
      }
      break
    case 'waiting':
      if (execution.value && data.data) {
        execution.value = { ...execution.value, ...data.data }
        if (data.data.quiz && !Object.keys(answersForm).length) {
          data.data.quiz.questions.forEach(q => {
            answersForm[q.id] = ''
          })
        }
      }
      closeSSE()
      break
    case 'complete':
      closeSSE()
      if (fileBrowserRef.value) {
        fileBrowserRef.value.loadFiles()
      }
      break
    case 'stream_error':
      logger.warn('工作流流式执行异常:', data.message)
      break
    case 'error':
      ElMessage.error(data.message || '工作流执行出错')
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
  execution.value = null
  Object.keys(answersForm).forEach(key => delete answersForm[key])
  workflowForm.query = ''
  showDetail.value = false
  stopPolling()
  closeSSE()
}

const viewTask = async (selectedTask) => {
  closeSSE()
  stopPolling()

  execution.value = selectedTask
  showDetail.value = true
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
        const freshActive = fresh.current_step
          && fresh.current_step !== 'waiting_for_answers'
          && fresh.current_step !== 'end'
          && fresh.current_step !== 'completed'
          && fresh.current_step !== 'failed'
        if (freshActive) {
          connectSSE(fresh.thread_id)
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
  execution.value = null
  showDetail.value = false
  Object.keys(answersForm).forEach(key => delete answersForm[key])
}

onUnmounted(() => {
  stopPolling()
  closeSSE()
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
