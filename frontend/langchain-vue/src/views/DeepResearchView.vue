<template>
  <div class="deep-research-view">
    <div class="view-content">
      <el-card class="start-card">
        <template #header>
          <div class="card-header">
            <span class="page-title">深度研究</span>
          </div>
        </template>
        <el-form :model="researchForm" label-width="120px">
          <el-form-item label="研究主题">
            <el-input
              v-model="researchForm.query"
              type="textarea"
              :rows="3"
              placeholder="请输入您想研究的主题..."
            />
          </el-form-item>
          <el-form-item label="启用网络搜索">
            <el-switch v-model="researchForm.enable_web_search" />
          </el-form-item>
          <el-form-item label="启用文档分析">
            <el-switch v-model="researchForm.enable_doc_analysis" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="isLoading" @click="startResearch">
              开始研究
            </el-button>
          </el-form-item>
        </el-form>
      </el-card>

      <el-card v-if="showTaskDetail && task" class="task-detail-card">
        <template #header>
          <div class="card-header">
            <span>研究任务详情</span>
            <div class="header-actions">
              <el-tag v-if="progressMessage" type="info" class="progress-message">
                {{ progressMessage }}
              </el-tag>
              <el-tag :type="getStatusType(task.status)">
                {{ getStatusText(task.status) }}
              </el-tag>
              <el-button link type="primary" size="small" @click="showTaskDetail = false">
                返回列表
              </el-button>
            </div>
          </div>
        </template>

        <el-descriptions :column="2" border>
          <el-descriptions-item label="任务ID">{{ task.task_id }}</el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ formatDate(task.created_at) }}</el-descriptions-item>
          <el-descriptions-item label="研究主题" :span="2">{{ task.query }}</el-descriptions-item>
          <el-descriptions-item label="网络搜索">
            <el-tag :type="task.enable_web_search ? 'success' : 'info'">
              {{ task.enable_web_search ? '已启用' : '未启用' }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="文档分析">
            <el-tag :type="task.enable_doc_analysis ? 'success' : 'info'">
              {{ task.enable_doc_analysis ? '已启用' : '未启用' }}
            </el-tag>
          </el-descriptions-item>
        </el-descriptions>

        <div v-if="task.status === 'running' || task.status === 'pending'" class="progress-section">
          <el-progress
            :percentage="progressPercentage"
            :status="task.status === 'pending' ? '' : undefined"
            :stroke-width="8"
            striped
            striped-flow
          />
          <p class="progress-hint">深度研究通常需要 5-10 分钟，请耐心等待...</p>
        </div>

        <div v-if="task.final_report" class="report-section">
          <h4>研究报告</h4>
          <div class="report-content">
            <MarkdownRenderer :content="task.final_report" />
          </div>
        </div>

        <el-divider />

        <div class="files-section">
          <h4>生成的文件</h4>
          <FileBrowser
            ref="fileBrowserRef"
            :task-id="task.task_id"
            :api="deepResearchAPI"
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
          module-type="deep-research"
          :api="deepResearchAPI"
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
import { deepResearchAPI } from '../api/client'
import { useUserStore } from '../stores/user'
import { ElMessage } from 'element-plus'
import TaskList from '../components/TaskList.vue'
import FileBrowser from '../components/FileBrowser.vue'
import MarkdownRenderer from '../components/MarkdownRenderer.vue'

const userStore = useUserStore()
const isLoading = ref(false)
const task = ref(null)
const showTaskDetail = ref(false)
const taskListRef = ref(null)
const fileBrowserRef = ref(null)
const progressMessage = ref('')
const elapsedSeconds = ref(0)
let pollingTimer = null
let eventSource = null
let elapsedTimer = null
let pollCount = 0
let currentPollInterval = 3000
const MAX_POLL_COUNT = 600
const BASE_POLL_INTERVAL = 3000
const MAX_POLL_INTERVAL = 30000
const POLL_BACKOFF_FACTOR = 1.5

const progressPercentage = computed(() => {
  if (!task.value) return 0
  if (task.value.status === 'completed') return 100
  if (task.value.status === 'failed') return 0
  if (task.value.status === 'pending') return 10
  if (task.value.status === 'running') {
    const maxSeconds = 600
    const pct = Math.min(90, 10 + (elapsedSeconds.value / maxSeconds) * 80)
    return Math.round(pct)
  }
  return 0
})

const researchForm = reactive({
  query: '',
  enable_web_search: true,
  enable_doc_analysis: false,
})

const statusOptions = [
  { value: 'pending',
  label: '待执行'
},
  {
    value: 'running',
    label: '执行中'
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

const getStatusType = (status) => {
  const typeMap = {
    pending: 'info',
    running: 'warning',
    completed: 'success',
    failed: 'danger',
  }
  return typeMap[status] || 'info'
}

const getStatusText = (status) => {
  const textMap = {
    pending: '待执行',
    running: '执行中',
    completed: '已完成',
    failed: '失败',
  }
  return textMap[status] || status
}

const pollTaskStatus = async () => {
  if (!task.value || task.value.status === 'completed' || task.value.status === 'failed') {
    stopPolling()
    if (fileBrowserRef.value) {
      fileBrowserRef.value.loadFiles()
    }
    return
  }

  pollCount++
  if (pollCount >= MAX_POLL_COUNT) {
    ElMessage.warning('研究任务轮询超时，请刷新页面查看最新状态')
    stopPolling()
    return
  }

  try {
    const response = await deepResearchAPI.getStatus(task.value.task_id)
    const responseData = response.data.data || response.data
    const prevStatus = task.value.status
    task.value = { ...task.value, ...responseData }

    if (task.value.status === 'completed' || task.value.status === 'failed') {
      stopPolling()
      if (fileBrowserRef.value) {
        fileBrowserRef.value.loadFiles()
      }
      return
    }

    if (prevStatus === 'pending' && task.value.status === 'running') {
      currentPollInterval = BASE_POLL_INTERVAL
    } else {
      currentPollInterval = Math.min(
        Math.floor(currentPollInterval * POLL_BACKOFF_FACTOR),
        MAX_POLL_INTERVAL
      )
    }

    pollingTimer = setTimeout(pollTaskStatus, currentPollInterval)
  } catch (error) {
    console.error('获取任务状态失败:', error)
    currentPollInterval = Math.min(
      Math.floor(currentPollInterval * POLL_BACKOFF_FACTOR),
      MAX_POLL_INTERVAL
    )
    pollingTimer = setTimeout(pollTaskStatus, currentPollInterval)
  }
}

const stopPolling = () => {
  if (pollingTimer) {
    clearTimeout(pollingTimer)
    pollingTimer = null
  }
  currentPollInterval = BASE_POLL_INTERVAL
}

const startResearch = async () => {
  if (!researchForm.query.trim()) {
    ElMessage.warning('请输入研究主题')
    return
  }

  isLoading.value = true
  task.value = null
  pollCount = 0
  currentPollInterval = BASE_POLL_INTERVAL
  elapsedSeconds.value = 0

  try {
    const response = await deepResearchAPI.start(researchForm)
    task.value = response.data.data || response.data
    showTaskDetail.value = true
    ElMessage.success('研究任务已启动')

    stopPolling()
    startElapsedTimer()
    connectSSE(task.value.task_id)
  } catch (error) {
    console.error('启动研究任务失败:', error)
    ElMessage.error('启动研究任务失败，请稍后重试')
  } finally {
    isLoading.value = false
  }
}

const startElapsedTimer = () => {
  stopElapsedTimer()
  elapsedTimer = setInterval(() => {
    elapsedSeconds.value++
  }, 1000)
}

const stopElapsedTimer = () => {
  if (elapsedTimer) {
    clearInterval(elapsedTimer)
    elapsedTimer = null
  }
}

const connectSSE = (taskId) => {
  closeSSE()

  const url = deepResearchAPI.streamUrl(taskId)
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
      if (task.value && task.value.status !== 'completed' && task.value.status !== 'failed') {
        pollingTimer = setTimeout(pollTaskStatus, currentPollInterval)
      }
    }
  } catch (e) {
    console.error('SSE连接失败，回退到轮询:', e)
    pollingTimer = setTimeout(pollTaskStatus, currentPollInterval)
  }
}

const handleSSEEvent = (data) => {
  switch (data.type) {
    case 'connected':
      progressMessage.value = '已连接，等待研究启动...'
      break
    case 'status_change':
      if (task.value) {
        task.value.status = data.status
        progressMessage.value = data.message || ''
        if (data.final_report) {
          task.value.final_report = data.final_report
        }
      }
      if (data.status === 'completed' || data.status === 'failed') {
        closeSSE()
        stopElapsedTimer()
        if (fileBrowserRef.value) {
          fileBrowserRef.value.loadFiles()
        }
      }
      break
    case 'step_update':
      progressMessage.value = data.step || ''
      break
    case 'done':
      closeSSE()
      stopElapsedTimer()
      break
    case 'timeout':
      progressMessage.value = '连接超时，正在回退到轮询模式...'
      closeSSE()
      pollingTimer = setTimeout(pollTaskStatus, currentPollInterval)
      break
    case 'error':
      ElMessage.error(data.message || '研究执行出错')
      closeSSE()
      stopElapsedTimer()
      break
  }
}

const closeSSE = () => {
  if (eventSource) {
    eventSource.close()
    eventSource = null
  }
}

const viewTask = (selectedTask) => {
  task.value = selectedTask
  showTaskDetail.value = true
}

const deleteTask = () => {
  task.value = null
  showTaskDetail.value = false
}

onUnmounted(() => {
  stopPolling()
  closeSSE()
  stopElapsedTimer()
})
</script>

<style scoped>
.deep-research-view {
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

.start-card {
  margin-bottom: 20px;
}

.task-detail-card,
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

.progress-message {
  animation: pulse-opacity 2s ease-in-out infinite;
}

@keyframes pulse-opacity {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.6; }
}

.progress-section {
  margin-top: 24px;
  padding: 16px;
  background: var(--el-fill-color-lighter);
  border-radius: 8px;
}

.progress-hint {
  margin: 12px 0 0 0;
  font-size: 13px;
  color: var(--el-text-color-secondary);
  text-align: center;
}

.report-section {
  margin-top: 24px;
}

.report-section h4 {
  margin: 0 0 16px 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.report-content {
  padding: 20px;
  background: var(--el-fill-color-lighter);
  border-radius: 4px;
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
  .deep-research-view {
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

  .report-content {
    padding: 12px;
  }

  .progress-section {
    padding: 12px;
  }
}

@media (max-width: 480px) {
  .deep-research-view {
    padding: 8px;
  }

  .page-title {
    font-size: 16px;
  }
}
</style>
