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
import { ref, reactive, onUnmounted } from 'vue'
import { workflowAPI } from '../api/client'
import { useUserStore } from '../stores/user'
import { ElMessage } from 'element-plus'
import TaskList from '../components/TaskList.vue'
import FileBrowser from '../components/FileBrowser.vue'

const userStore = useUserStore()
const isLoading = ref(false)
const isSubmitting = ref(false)
const execution = ref(null)
const showDetail = ref(false)
const answersForm = reactive({})
const fileBrowserRef = ref(null)
const currentStepMessage = ref('')
let pollingTimer = null
let eventSource = null
let currentPollInterval = 3000
const BASE_POLL_INTERVAL = 3000
const MAX_POLL_INTERVAL = 30000
const POLL_BACKOFF_FACTOR = 1.5

const workflowForm = reactive({
  query: '',
})

const statusOptions = [
  {
    value: 'start',
    label: '准备中'
  },
  {
    value: 'planner',
    label: '生成学习计划'
  },
  {
    value: 'retrieval',
    label: '检索资料'
  },
  {
    value: 'quiz_generator',
    label: '生成练习题'
  },
  {
    value: 'waiting_for_answers',
    label: '等待答题'
  },
  {
    value: 'grading',
    label: '评分中'
  },
  {
    value: 'feedback',
    label: '生成反馈'
  },
  {
    value: 'end',
    label: '已结束'
  },
  {
    value: 'completed',
    label: '已完成'
  },
  {
    value: 'failed',
    label: '失败'
  },
]

const formatDate = (dateStr) => {
  if (!dateStr) return '-'
  const date = new Date(dateStr)
  return date.toLocaleString('zh-CN')
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
    console.error('获取工作流状态失败:', error)
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
    console.error('启动工作流失败:', error)
    ElMessage.error('启动工作流失败，请稍后重试')
  } finally {
    isLoading.value = false
  }
}

const connectSSE = (threadId) => {
  closeSSE()

  const url = workflowAPI.streamUrl(threadId)
  const token = userStore.token
  const separator = url.includes('?') ? '&' : '?'
  const fullUrl = token ? `${url}${separator}token=${token}` : url

  try {
    eventSource = new EventSource(fullUrl)

    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        handleSSEEvent(data)
      } catch (e) {
        console.error('解析SSE数据失败:', e)
      }
    }

    eventSource.onerror = () => {
      closeSSE()
      if (execution.value) {
        const step = execution.value.current_step
        if (step !== 'waiting_for_answers' && step !== 'end' && step !== 'completed') {
          pollingTimer = setTimeout(pollExecutionStatus, currentPollInterval)
        }
      }
    }
  } catch (e) {
    console.error('SSE连接失败，回退到轮询:', e)
    pollingTimer = setTimeout(pollExecutionStatus, currentPollInterval)
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
    case 'error':
      ElMessage.error(data.message || '工作流执行出错')
      closeSSE()
      break
  }
}

const closeSSE = () => {
  if (eventSource) {
    eventSource.close()
    eventSource = null
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
    console.error('提交答案失败:', error)
    ElMessage.error('提交答案失败，请稍后重试')
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

const viewTask = (selectedTask) => {
  execution.value = selectedTask
  showDetail.value = true
  if (selectedTask.quiz && !Object.keys(answersForm).length) {
    selectedTask.quiz.questions.forEach(q => {
      answersForm[q.id] = ''
    })
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

@media (max-width: 768px) {
  .workflow-view {
    padding: 12px;
  }

  .card-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }

  .header-actions {
    width: 100%;
    flex-wrap: wrap;
  }

  .plan-card h4 {
    font-size: 16px;
  }

  .question-text {
    font-size: 14px;
  }
}

@media (max-width: 480px) {
  .workflow-view {
    padding: 8px;
  }

  .page-title {
    font-size: 16px;
  }
}
</style>
