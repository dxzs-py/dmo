<template>
  <div class="workflow-view">
    <div class="view-content">
      <el-card class="start-card">
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
          <el-form-item label="工作流类型">
            <el-select v-model="workflowForm.workflow_type" placeholder="请选择工作流">
              <el-option label="学习工作流" value="study_flow" />
            </el-select>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="startWorkflow" :loading="isLoading">
              启动工作流
            </el-button>
          </el-form-item>
        </el-form>
      </el-card>
      
      <el-card v-if="execution" class="status-card">
        <template #header>
          <div class="card-header">
            <span>工作流状态</span>
            <el-tag :type="getStatusType(execution.status)">
              {{ getStatusText(execution.status) }}
            </el-tag>
          </div>
        </template>
        
        <el-descriptions :column="1" border>
          <el-descriptions-item label="线程ID">{{ execution.thread_id }}</el-descriptions-item>
          <el-descriptions-item label="查询">{{ execution.query }}</el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ execution.created_at }}</el-descriptions-item>
          <el-descriptions-item label="更新时间">{{ execution.updated_at }}</el-descriptions-item>
        </el-descriptions>
        
        <div v-if="execution.result" class="result-section">
          <h4>执行结果</h4>
          <pre>{{ JSON.stringify(execution.result, null, 2) }}</pre>
        </div>
      </el-card>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onUnmounted } from 'vue'
import { workflowAPI } from '../api/client'
import { ElMessage } from 'element-plus'

const isLoading = ref(false)
const execution = ref(null)
let pollingInterval = null

const workflowForm = reactive({
  query: '',
  workflow_type: 'study_flow',
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

const pollExecutionStatus = async () => {
  if (!execution.value || execution.value.status === 'completed' || execution.value.status === 'failed') {
    if (pollingInterval) {
      clearInterval(pollingInterval)
      pollingInterval = null
    }
    return
  }
  
  try {
    const response = await workflowAPI.getState(execution.value.thread_id)
    execution.value = { ...execution.value, ...response.data }
  } catch (error) {
    console.error('获取工作流状态失败:', error)
  }
}

const startWorkflow = async () => {
  if (!workflowForm.query.trim()) {
    ElMessage.warning('请输入学习主题')
    return
  }
  
  isLoading.value = true
  execution.value = null
  
  try {
    const response = await workflowAPI.start(workflowForm)
    execution.value = response.data
    ElMessage.success('工作流已启动')
    
    pollingInterval = setInterval(pollExecutionStatus, 2000)
  } catch (error) {
    console.error('启动工作流失败:', error)
    ElMessage.error('启动工作流失败，请稍后重试')
  } finally {
    isLoading.value = false
  }
}

onUnmounted(() => {
  if (pollingInterval) {
    clearInterval(pollingInterval)
  }
})
</script>

<style scoped>
.workflow-view {
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

.status-card {
  margin-top: 20px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.result-section {
  margin-top: 20px;
}

.result-section h4 {
  margin-bottom: 10px;
}

.result-section pre {
  background: var(--el-fill-color-lighter);
  padding: 15px;
  border-radius: 4px;
  overflow-x: auto;
}
</style>
