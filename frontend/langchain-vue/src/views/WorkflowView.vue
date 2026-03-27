<template>
  <div class="workflow-view">
    <el-container>
      <el-header>
        <h2>学习工作流</h2>
      </el-header>
      
      <el-main>
        <el-card class="start-card">
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
      </el-main>
    </el-container>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { workflowAPI } from '../api/client'
import { ElMessage } from 'element-plus'

const isLoading = ref(false)
const execution = ref(null)

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
  } catch (error) {
    console.error('启动工作流失败:', error)
    ElMessage.error('启动工作流失败，请稍后重试')
  } finally {
    isLoading.value = false
  }
}
</script>

<style scoped>
.workflow-view {
  height: 100vh;
  display: flex;
  flex-direction: column;
}

.el-container {
  height: 100%;
}

.el-header {
  display: flex;
  align-items: center;
  background: #f5f7fa;
  border-bottom: 1px solid #e4e7ed;
}

.el-header h2 {
  margin: 0;
  font-size: 20px;
  color: #303133;
}

.el-main {
  padding: 20px;
  overflow-y: auto;
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
  background: #f5f7fa;
  padding: 15px;
  border-radius: 4px;
  overflow-x: auto;
}
</style>
