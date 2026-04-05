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
            <el-button type="primary" @click="startResearch" :loading="isLoading">
              开始研究
            </el-button>
          </el-form-item>
        </el-form>
      </el-card>
      
      <el-card v-if="task" class="task-card">
        <template #header>
          <div class="card-header">
            <span>研究任务</span>
            <el-tag :type="getStatusType(task.status)">
              {{ getStatusText(task.status) }}
            </el-tag>
          </div>
        </template>
        
        <el-descriptions :column="1" border>
          <el-descriptions-item label="任务ID">{{ task.task_id }}</el-descriptions-item>
          <el-descriptions-item label="研究主题">{{ task.query }}</el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ task.created_at }}</el-descriptions-item>
          <el-descriptions-item label="更新时间">{{ task.updated_at }}</el-descriptions-item>
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
        
        <div v-if="task.final_report" class="report-section">
          <h4>研究报告</h4>
          <div class="report-content" v-html="formatReport(task.final_report)"></div>
        </div>
      </el-card>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onUnmounted } from 'vue'
import { deepResearchAPI } from '../api/client'
import { ElMessage } from 'element-plus'

const isLoading = ref(false)
const task = ref(null)
let pollingInterval = null
let pollCount = 0
const MAX_POLL_COUNT = 600

const researchForm = reactive({
  query: '',
  enable_web_search: true,
  enable_doc_analysis: false,
})

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

const formatReport = (content) => {
  return content.replace(/\n/g, '<br>')
}

const pollTaskStatus = async () => {
  if (!task.value || task.value.status === 'completed' || task.value.status === 'failed') {
    stopPolling()
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
    task.value = { ...task.value, ...responseData }

    if (task.value.status === 'completed' || task.value.status === 'failed') {
      stopPolling()
    }
  } catch (error) {
    console.error('获取任务状态失败:', error)
  }
}

const stopPolling = () => {
  if (pollingInterval) {
    clearInterval(pollingInterval)
    pollingInterval = null
  }
}

const startResearch = async () => {
  if (!researchForm.query.trim()) {
    ElMessage.warning('请输入研究主题')
    return
  }

  isLoading.value = true
  task.value = null
  pollCount = 0

  try {
    const response = await deepResearchAPI.start(researchForm)
    task.value = response.data.data || response.data
    ElMessage.success('研究任务已启动')

    stopPolling()
    pollingInterval = setInterval(pollTaskStatus, 3000)
  } catch (error) {
    console.error('启动研究任务失败:', error)
    ElMessage.error('启动研究任务失败，请稍后重试')
  } finally {
    isLoading.value = false
  }
}

onUnmounted(() => {
  stopPolling()
})
</script>

<style scoped>
.deep-research-view {
  height: 100%;
  padding: 24px;
  overflow-y: auto;
}

.view-content {
  max-width: 900px;
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

.task-card {
  margin-top: 20px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.report-section {
  margin-top: 20px;
}

.report-section h4 {
  margin-bottom: 10px;
}

.report-content {
  background: var(--el-fill-color-lighter);
  padding: 20px;
  border-radius: 4px;
  line-height: 1.8;
  white-space: pre-wrap;
}
</style>
