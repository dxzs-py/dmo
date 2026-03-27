<template>
  <div class="rag-view">
    <el-container>
      <el-header>
        <h2>RAG 知识库查询</h2>
      </el-header>
      
      <el-main>
        <el-card class="query-card">
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
      </el-main>
    </el-container>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { ragAPI } from '../api/client'
import { ElMessage } from 'element-plus'

const isLoading = ref(false)
const result = ref(null)

const queryForm = reactive({
  index_name: 'test_index',
  query: '',
  k: 4,
  use_rag_agent: true,
})

const executeQuery = async () => {
  if (!queryForm.query.trim()) {
    ElMessage.warning('请输入查询内容')
    return
  }
  
  isLoading.value = true
  result.value = null
  
  try {
    const response = await ragAPI.query(queryForm)
    result.value = response.data
    ElMessage.success('查询成功')
  } catch (error) {
    console.error('查询失败:', error)
    ElMessage.error('查询失败，请稍后重试')
    result.value = { success: false, error: error.message }
  } finally {
    isLoading.value = false
  }
}
</script>

<style scoped>
.rag-view {
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

.query-card {
  margin-bottom: 20px;
}

.result-card {
  margin-top: 20px;
}

.answer {
  background: #f5f7fa;
  padding: 15px;
  border-radius: 4px;
  line-height: 1.8;
  white-space: pre-wrap;
}

.error {
  margin-top: 20px;
}
</style>
