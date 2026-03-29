<template>
  <div class="rag-view">
    <div class="view-content">
      <el-card class="query-card">
        <template #header>
          <div class="card-header">
            <span class="page-title">RAG 知识库查询</span>
          </div>
        </template>
        <el-form :model="queryForm" label-width="100px">
          <el-form-item label="索引名称">
            <el-select v-model="queryForm.index_name" placeholder="请选择索引">
              <el-option label="test_index" value="test_index" />
            </el-select>
          </el-form-item>
          <el-form-item label="查询内容">
            <el-input
              v-model="queryForm.query"
              type="textarea"
              :rows="3"
              placeholder="请输入您的查询..."
              clearable
            />
          </el-form-item>
          <el-form-item label="返回结果数">
            <el-input-number v-model="queryForm.k" :min="1" :max="10" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="executeQuery" :loading="isLoading">
              查询
            </el-button>
          </el-form-item>
        </el-form>
      </el-card>
      
      <el-alert
        v-if="errorMessage"
        :title="errorMessage"
        type="error"
        :closable="true"
        @close="errorMessage = ''"
        style="margin-bottom: 20px;"
      />
      
      <el-card v-if="result" class="result-card">
        <template #header>
          <div class="card-header">
            <span>查询结果</span>
          </div>
        </template>
        
        <div v-if="result.success">
          <h4>答案</h4>
          <p class="answer">{{ result.answer }}</p>
          
          <h4>参考来源</h4>
          <el-collapse>
            <el-collapse-item
              v-for="(source, index) in result.sources"
              :key="index"
              :title="`来源 ${index + 1}`"
            >
              <p>{{ source.content }}</p>
            </el-collapse-item>
          </el-collapse>
        </div>
        
        <div v-else class="error">
          <el-alert type="error" :title="result.error || '查询失败'" />
        </div>
      </el-card>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { ragAPI } from '../api/client'
import { ElMessage, ElAlert } from 'element-plus'

const isLoading = ref(false)
const result = ref(null)
const errorMessage = ref('')

const queryForm = reactive({
  index_name: 'test_index',
  query: '',
  k: 4,
  use_rag_agent: true,
})

const validateForm = () => {
  errorMessage.value = ''
  
  if (!queryForm.index_name || queryForm.index_name.trim() === '') {
    errorMessage.value = '请选择或输入索引名称'
    return false
  }
  
  if (!queryForm.query || queryForm.query.trim() === '') {
    errorMessage.value = '请输入查询内容'
    return false
  }
  
  if (queryForm.k < 1 || queryForm.k > 10) {
    errorMessage.value = '返回结果数应在 1-10 之间'
    return false
  }
  
  return true
}

const executeQuery = async () => {
  if (!validateForm()) {
    ElMessage.warning(errorMessage.value)
    return
  }
  
  isLoading.value = true
  result.value = null
  errorMessage.value = ''
  
  try {
    const response = await ragAPI.query(queryForm)
    result.value = response.data
    ElMessage.success('查询成功')
  } catch (error) {
    console.error('查询失败:', error)
    let errorMsg = '查询失败，请稍后重试'
    
    if (error.response) {
      if (error.response.status === 404) {
        errorMsg = '索引不存在，请检查索引名称'
      } else if (error.response.status === 500) {
        errorMsg = '服务器内部错误，请联系管理员'
      } else if (error.response.data && error.response.data.detail) {
        errorMsg = error.response.data.detail
      }
    } else if (error.request) {
      errorMsg = '网络连接失败，请检查网络设置'
    }
    
    errorMessage.value = errorMsg
    ElMessage.error(errorMsg)
    result.value = { success: false, error: errorMsg }
  } finally {
    isLoading.value = false
  }
}
</script>

<style scoped>
.rag-view {
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

.query-card {
  margin-bottom: 20px;
}

.result-card {
  margin-top: 20px;
}

.answer {
  background: var(--el-fill-color-lighter);
  padding: 15px;
  border-radius: 4px;
  line-height: 1.8;
  white-space: pre-wrap;
}

.error {
  margin-top: 20px;
}
</style>
