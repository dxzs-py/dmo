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
            <el-switch v-model="researchForm.enable_doc_analysis" @change="onDocAnalysisChange" />
          </el-form-item>
          <el-form-item v-if="researchForm.enable_doc_analysis" label="选择知识库">
            <div class="kb-selector">
              <div class="kb-selector-header">
                <el-input
                  v-model="kbSearchQuery"
                  placeholder="搜索知识库..."
                  clearable
                  size="small"
                  class="kb-search-input"
                  @input="filterKnowledgeBases"
                />
                <el-button size="small" @click="refreshKnowledgeBases" :loading="kbLoading">
                  刷新
                </el-button>
              </div>
              <div v-if="kbLoading" class="kb-loading">
                <el-icon class="is-loading"><Loading /></el-icon>
                <span>加载知识库列表...</span>
              </div>
              <div v-else-if="filteredKnowledgeBases.length === 0" class="kb-empty">
                <span v-if="kbSearchQuery">未找到匹配的知识库</span>
                <span v-else>暂无可用知识库，请先在知识库页面创建并上传文档</span>
              </div>
              <div v-else class="kb-list">
                <el-checkbox-group v-model="researchForm.knowledge_base_ids">
                  <div
                    v-for="kb in filteredKnowledgeBases"
                    :key="kb.id"
                    class="kb-item"
                  >
                    <el-checkbox :label="kb.id" :value="kb.id">
                      <div class="kb-item-content">
                        <span class="kb-name">{{ kb.name }}</span>
                        <span class="kb-meta">
                          <el-tag size="small" type="info">{{ kb.chunk_count || 0 }} 文档块</el-tag>
                          <span v-if="kb.description" class="kb-desc">{{ kb.description }}</span>
                        </span>
                      </div>
                    </el-checkbox>
                  </div>
                </el-checkbox-group>
              </div>
              <div v-if="researchForm.knowledge_base_ids.length > 0" class="kb-selected-summary">
                已选择 {{ researchForm.knowledge_base_ids.length }} 个知识库
              </div>
            </div>
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
          <el-descriptions-item
            v-if="task.enable_doc_analysis && task.knowledge_base_ids && task.knowledge_base_ids.length"
            label="关联知识库"
            :span="2"
          >
            <el-tag
              v-for="kbId in task.knowledge_base_ids"
              :key="kbId"
              size="small"
              class="kb-tag"
            >
              {{ kbId }}
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
          <div class="report-header">
            <h4>研究报告</h4>
            <AiOpenInChat label="在聊天中讨论" @click="openInChat" />
          </div>
          <div class="report-content">
            <MarkdownRenderer :content="task.final_report" />
          </div>

          <div v-if="docAnalysisFile" class="analysis-section">
            <el-divider />
            <div class="analysis-header">
              <h4>文档分析详情</h4>
              <el-button
                v-if="!docAnalysisContent"
                size="small"
                @click="loadDocAnalysis"
                :loading="docAnalysisLoading"
              >
                查看分析依据
              </el-button>
            </div>
            <div v-if="docAnalysisLoading" class="analysis-loading">
              <el-icon class="is-loading"><Loading /></el-icon>
              <span>加载分析详情...</span>
            </div>
            <div v-else-if="docAnalysisContent" class="analysis-content">
              <MarkdownRenderer :content="docAnalysisContent" />
            </div>
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
            <el-button text type="primary" @click="showFileSearch = !showFileSearch">
              {{ showFileSearch ? '收起搜索' : '全局文件搜索' }}
            </el-button>
          </div>
        </template>

        <div v-if="showFileSearch" class="file-search-section">
          <el-input
            v-model="fileSearchQuery"
            placeholder="搜索所有研究任务的文件..."
            @keyup.enter="handleFileSearch"
            clearable
          >
            <template #append>
              <el-button @click="handleFileSearch" :loading="fileSearchLoading">搜索</el-button>
            </template>
          </el-input>
          <div v-if="fileSearchResults.length" class="file-search-results">
            <el-table :data="fileSearchResults" style="width: 100%" size="small">
              <el-table-column prop="filename" label="文件名" />
              <el-table-column prop="task_id" label="任务ID" width="160" />
              <el-table-column prop="size" label="大小" width="100">
                <template #default="scope">
                  {{ scope.row.size ? formatFileSize(scope.row.size) : '-' }}
                </template>
              </el-table-column>
              <el-table-column label="操作" width="100">
                <template #default="scope">
                  <el-button link type="primary" size="small" @click="viewTask({ task_id: scope.row.task_id })">
                    查看任务
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
          </div>
          <el-empty v-else-if="fileSearchSearched && !fileSearchLoading" description="未找到匹配的文件" :image-size="60" />
        </div>

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
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { deepResearchAPI, knowledgeAPI } from '../api'
import { readSSEStream } from '../utils/sse'
import { ElMessage } from 'element-plus'
import { Loading } from '@element-plus/icons-vue'
import TaskList from '../components/chat/TaskList.vue'
import FileBrowser from '../components/chat/FileBrowser.vue'
import MarkdownRenderer from '../components/common/MarkdownRenderer.vue'
import AiOpenInChat from '../components/ai-elements/AiOpenInChat.vue'
import { formatDate as _formatDate, formatFileSize } from '../utils/format'
import { logger } from '../utils/logger'

const isLoading = ref(false)
const router = useRouter()
const task = ref(null)
const showTaskDetail = ref(false)
const taskListRef = ref(null)
const fileBrowserRef = ref(null)
const progressMessage = ref('')
const elapsedSeconds = ref(0)
const showFileSearch = ref(false)
const fileSearchQuery = ref('')
const fileSearchResults = ref([])
const fileSearchLoading = ref(false)
const fileSearchSearched = ref(false)

const knowledgeBases = ref([])
const kbLoading = ref(false)
const kbSearchQuery = ref('')
const filteredKnowledgeBases = ref([])

const docAnalysisContent = ref(null)
const docAnalysisLoading = ref(false)
const docAnalysisFile = ref(null)

let pollingTimer = null
let sseAbortController = null
let sseReaderActive = false
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
  knowledge_base_ids: [],
})

const statusOptions = [
  { value: 'pending', label: '待执行' },
  { value: 'running', label: '执行中' },
  { value: 'completed', label: '已完成' },
  { value: 'failed', label: '失败' },
]

const onDocAnalysisChange = (val) => {
  if (val && knowledgeBases.value.length === 0) {
    refreshKnowledgeBases()
  }
  if (!val) {
    researchForm.knowledge_base_ids = []
  }
}

const refreshKnowledgeBases = async () => {
  kbLoading.value = true
  try {
    const response = await knowledgeAPI.getKnowledgeBases()
    const data = response.data?.data || response.data
    knowledgeBases.value = data?.items || []
    filterKnowledgeBases()
  } catch (error) {
    logger.error('加载知识库列表失败:', error)
    ElMessage.error('加载知识库列表失败')
  } finally {
    kbLoading.value = false
  }
}

const filterKnowledgeBases = () => {
  const query = kbSearchQuery.value.toLowerCase().trim()
  if (!query) {
    filteredKnowledgeBases.value = [...knowledgeBases.value]
  } else {
    filteredKnowledgeBases.value = knowledgeBases.value.filter(
      kb => kb.name?.toLowerCase().includes(query) || kb.description?.toLowerCase().includes(query)
    )
  }
}

const loadDocAnalysis = async () => {
  if (!task.value?.task_id) return
  docAnalysisLoading.value = true
  try {
    const response = await deepResearchAPI.getFileContent(task.value.task_id, 'notes/doc_analysis.md')
    const data = response.data?.data || response.data
    docAnalysisContent.value = data?.content || data || ''
  } catch (error) {
    logger.warn('加载文档分析详情失败:', error)
    docAnalysisContent.value = null
  } finally {
    docAnalysisLoading.value = false
  }
}

const checkDocAnalysisFile = () => {
  docAnalysisFile.value = null
  docAnalysisContent.value = null
  if (task.value?.enable_doc_analysis && task.value?.task_id) {
    docAnalysisFile.value = 'notes/doc_analysis.md'
  }
}

const formatDate = (dateStr) => {
  if (!dateStr) return '-'
  return _formatDate(dateStr)
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
      checkDocAnalysisFile()
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
    logger.error('获取任务状态失败:', error)
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

  if (researchForm.enable_doc_analysis && researchForm.knowledge_base_ids.length === 0) {
    ElMessage.warning('启用文档分析时，请至少选择一个知识库')
    return
  }

  isLoading.value = true
  task.value = null
  pollCount = 0
  currentPollInterval = BASE_POLL_INTERVAL
  elapsedSeconds.value = 0
  docAnalysisContent.value = null
  docAnalysisFile.value = null

  try {
    const response = await deepResearchAPI.start(researchForm)
    task.value = response.data.data || response.data
    showTaskDetail.value = true
    ElMessage.success('研究任务已启动')

    stopPolling()
    startElapsedTimer()
    connectSSE(task.value.task_id)
  } catch (error) {
    logger.error('启动研究任务失败:', error)
    const detail = error.response?.data?.details || error.response?.data?.message
    if (detail) {
      ElMessage.error(detail)
    } else {
      ElMessage.error('启动研究任务失败，请稍后重试')
    }
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

const connectSSE = async (taskId) => {
  closeSSE()

  sseAbortController = new AbortController()
  sseReaderActive = true

  try {
    const response = await deepResearchAPI.streamFetch(taskId, {
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
      } catch { /* ignore parse error */ }
      throw new Error(errorMsg)
    }

    await readSSEStream(response, (data) => {
      if (!sseReaderActive) return
      handleSSEEvent(data)
    }, sseAbortController.signal)

    if (sseReaderActive && task.value && task.value.status !== 'completed' && task.value.status !== 'failed') {
      pollingTimer = setTimeout(pollTaskStatus, currentPollInterval)
    }
  } catch (error) {
    if (error.name === 'AbortError') {
      return
    }
    logger.error('SSE连接失败，回退到轮询:', error)
    if (task.value && task.value.status !== 'completed' && task.value.status !== 'failed') {
      pollingTimer = setTimeout(pollTaskStatus, currentPollInterval)
    }
  } finally {
    sseReaderActive = false
    sseAbortController = null
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
        checkDocAnalysisFile()
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
  sseReaderActive = false
  if (sseAbortController) {
    sseAbortController.abort()
    sseAbortController = null
  }
}

const viewTask = async (selectedTask) => {
  closeSSE()
  stopPolling()
  stopElapsedTimer()

  task.value = selectedTask
  showTaskDetail.value = true
  checkDocAnalysisFile()

  if (selectedTask.status === 'running' || selectedTask.status === 'pending') {
    startElapsedTimer()
    connectSSE(selectedTask.task_id)
  } else if (selectedTask.task_id) {
    try {
      const resp = await deepResearchAPI.getStatus(selectedTask.task_id)
      const fresh = resp.data?.data || resp.data
      if (fresh) {
        task.value = { ...selectedTask, ...fresh }
        if (fresh.status === 'running' || fresh.status === 'pending') {
          startElapsedTimer()
          connectSSE(fresh.task_id)
        }
      }
    } catch (e) {
      logger.warn('获取任务最新状态失败:', e)
    }
  }
}

const deleteTask = () => {
  task.value = null
  showTaskDetail.value = false
  docAnalysisContent.value = null
  docAnalysisFile.value = null
}

const openInChat = () => {
  if (!task.value?.query) return
  router.push({
    path: '/chat',
    query: { q: `关于"${task.value.query}"的深度研究，请帮我进一步分析` }
  })
}

const handleFileSearch = async () => {
  if (!fileSearchQuery.value.trim()) return
  fileSearchLoading.value = true
  fileSearchSearched.value = true
  try {
    const response = await deepResearchAPI.searchFiles(fileSearchQuery.value)
    if (response.data?.code === 200) {
      fileSearchResults.value = response.data.data?.files || response.data.data?.items || []
    }
  } catch (error) {
    logger.error('文件搜索失败:', error)
    ElMessage.error('文件搜索失败')
  } finally {
    fileSearchLoading.value = false
  }
}

onMounted(() => {
  refreshKnowledgeBases()
})

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

.report-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.report-header h4 {
  margin: 0;
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

.kb-selector {
  width: 100%;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 12px;
  background: var(--el-fill-color-lighter);
}

.kb-selector-header {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}

.kb-search-input {
  flex: 1;
}

.kb-loading,
.kb-empty {
  padding: 20px;
  text-align: center;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
}

.kb-list {
  max-height: 240px;
  overflow-y: auto;
}

.kb-item {
  padding: 8px 4px;
  border-bottom: 1px solid var(--el-border-color-extra-light);
}

.kb-item:last-child {
  border-bottom: none;
}

.kb-item-content {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.kb-name {
  font-weight: 500;
  font-size: 14px;
}

.kb-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.kb-desc {
  max-width: 300px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.kb-selected-summary {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--el-border-color-extra-light);
  font-size: 12px;
  color: var(--el-color-primary);
}

.kb-tag {
  margin-right: 6px;
  margin-bottom: 4px;
}

.analysis-section {
  margin-top: 8px;
}

.analysis-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.analysis-header h4 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.analysis-loading {
  padding: 20px;
  text-align: center;
  color: var(--el-text-color-secondary);
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
}

.analysis-content {
  padding: 16px;
  background: var(--el-fill-color-lighter);
  border-radius: 4px;
  border-left: 3px solid var(--el-color-primary);
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

.file-search-section {
  margin-bottom: 16px;
  padding: 16px;
  background: var(--el-fill-color-lighter);
  border-radius: 8px;
}

.file-search-results {
  margin-top: 12px;
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

  .kb-desc {
    max-width: 160px;
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
