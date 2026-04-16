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
            <el-select v-model="queryForm.index_name" placeholder="请选择索引" :loading="isLoadingIndexes">
              <el-option
                v-for="item in availableIndexes"
                :key="item.name"
                :label="item.name"
                :value="item.name"
              />
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
            <el-button type="primary" :loading="isLoading" @click="executeQuery">
              查询
            </el-button>
          </el-form-item>
        </el-form>
        
        <el-divider />
        
        <div class="upload-section">
          <h4 class="upload-title">上传文档到索引</h4>
          <el-upload
            ref="uploadRef"
            :auto-upload="false"
            :on-change="handleFileChange"
            :show-file-list="true"
            :limit="1"
            accept=".txt,.md,.pdf,.doc,.docx"
          >
            <el-button type="primary">选择文件</el-button>
            <template #tip>
              <div class="el-upload__tip">
                支持上传 .txt, .md, .pdf, .doc, .docx 格式文件，选中后点击「上传并索引」
              </div>
            </template>
          </el-upload>
          <div class="upload-actions">
            <el-button 
              type="success" 
              :disabled="!selectedFile || !queryForm.index_name" 
              :loading="isUploading"
              @click="handleUpload"
            >
              上传并索引
            </el-button>
            <el-button @click="clearUpload">清除</el-button>
          </div>
        </div>
      </el-card>
      
      <el-alert
        v-if="errorMessage"
        :title="errorMessage"
        type="error"
        :closable="true"
        style="margin-bottom: 20px;"
        @close="errorMessage = ''"
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
import { ref, reactive, onMounted } from 'vue'
import { ragAPI } from '../api/client'
import { ElMessage, ElAlert } from 'element-plus'

const isLoading = ref(false)
const isLoadingIndexes = ref(false)
const isUploading = ref(false)
const result = ref(null)
const errorMessage = ref('')
const availableIndexes = ref([{ name: 'test_index' }])
const uploadRef = ref(null)
const selectedFile = ref(null)

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

const fetchIndexes = async () => {
  isLoadingIndexes.value = true
  try {
    const response = await ragAPI.getIndexes()
    const data = response.data
    let indexes = []
    if (data.code === 200 && data.data) {
      indexes = Array.isArray(data.data) ? data.data : data.data.indexes || []
    } else if (data.indexes) {
      indexes = data.indexes
    }
    availableIndexes.value = indexes
      .map(item => (typeof item === 'string' ? { name: item } : item))
      .filter(item => item && item.name)
    if (availableIndexes.value.length > 0 && !availableIndexes.value.some(item => item.name === queryForm.index_name)) {
      queryForm.index_name = availableIndexes.value[0].name
    }
  } catch (error) {
    console.error('获取索引列表失败:', error)
  } finally {
    isLoadingIndexes.value = false
  }
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
    result.value = response.data.data || response.data
    ElMessage.success('查询成功')
  } catch (error) {
    console.error('查询失败:', error)
    let errorMsg = '查询失败，请稍后重试'

    if (error.response) {
      if (error.response.status === 404) {
        errorMsg = '索引不存在，请检查索引名称'
      } else if (error.response.status === 500) {
        errorMsg = '服务器内部错误，请联系管理员'
      } else if (error.response.data && error.response.data.message) {
        errorMsg = error.response.data.message
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

const handleFileChange = (file) => {
  selectedFile.value = file.raw
}

const clearUpload = () => {
  selectedFile.value = null
  if (uploadRef.value) {
    uploadRef.value.clearFiles()
  }
}

const handleUpload = async () => {
  if (!selectedFile.value) {
    ElMessage.warning('请先选择文件')
    return
  }
  if (!queryForm.index_name) {
    ElMessage.warning('请先选择索引')
    return
  }

  isUploading.value = true
  try {
    await ragAPI.uploadDocument(queryForm.index_name, selectedFile.value)
    ElMessage.success('文件上传并索引成功！')
    clearUpload()
  } catch (error) {
    console.error('上传失败:', error)
    let errorMsg = '上传失败'
    if (error.response?.data?.message) {
      errorMsg = error.response.data.message
    } else if (error.response?.status === 404) {
      errorMsg = '索引不存在'
    }
    ElMessage.error(errorMsg)
  } finally {
    isUploading.value = false
  }
}

onMounted(() => {
  fetchIndexes()
})
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

.upload-section {
  margin-top: 20px;
}

.upload-title {
  margin: 0 0 16px 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.upload-actions {
  margin-top: 16px;
  display: flex;
  gap: 12px;
}
</style>
