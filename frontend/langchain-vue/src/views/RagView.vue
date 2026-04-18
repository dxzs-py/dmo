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
          <el-form-item label="选择索引">
            <div class="index-selector">
            <el-select 
                v-model="selectedIndexName" 
                placeholder="请选择或创建索引"
                :loading="isLoadingIndexes"
                style="width: 100%"
                clearable
                @change="handleIndexChange"
            >
                <el-option
                    v-for="index in availableIndexes"
                    :key="index.name"
                    :label="`${index.name} (${index.num_documents} 文档)`"
                    :value="index.name"
                >
                    <div class="index-option">
                        <span class="index-name">{{ index.name }}</span>
                        <el-tag v-if="index.description" type="info" size="small" style="margin-left: 10px">
                            {{ index.description }}
                        </el-tag>
                        <el-tag 
                            v-if="index.num_documents === 0" 
                            type="warning" 
                            size="small" 
                            style="margin-left: auto"
                        >
                            空索引
                        </el-tag>
                        <span v-else class="index-count" style="margin-left: auto">{{ index.num_documents }} 文档</span>
                    </div>
                </el-option>
            </el-select>
              
              <el-button 
                type="primary" 
                style="margin-left: 10px"
                @click="showCreateDialog = true"
              >
                + 创建索引
              </el-button>
              
              <el-button 
                type="danger" 
                style="margin-left: 10px"
                @click="handleDeleteIndex"
                :disabled="!selectedIndexName"
                :loading="isDeletingIndex"
              >
                删除索引
              </el-button>
            </div>
            <div v-if="selectedIndexName && selectedIndex.num_documents === 0" class="empty-index-hint">
              <el-alert type="warning" :closable="false" size="small">
                此索引暂无文档，请先在下方上传文档后再进行查询
              </el-alert>
            </div>
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
            <el-button type="primary" :loading="isLoading" @click="executeQuery" :disabled="!selectedIndexName">
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
              :disabled="!selectedFile || !selectedIndexName" 
              :loading="isUploading"
              @click="handleUpload"
            >
              上传并索引
            </el-button>
            <el-button @click="clearUpload">清除</el-button>
          </div>
        </div>
        
        <el-divider />
        
        <div class="files-section" v-if="selectedIndexName">
          <h4 class="files-title">
            已上传文件
            <el-button 
              link 
              @click="loadFiles" 
              :loading="isLoadingFiles"
              style="margin-left: 10px"
            >
              刷新
            </el-button>
          </h4>
          <div v-if="files.length > 0" class="files-list">
            <div v-for="file in files" :key="file.name" class="file-item">
              <div class="file-info">
                <span class="file-name">{{ file.name }}</span>
                <span class="file-meta">
                  {{ formatFileSize(file.size) }} · {{ formatDate(file.uploaded_at) }}
                </span>
              </div>
              <el-button 
                type="danger" 
                size="small" 
                @click="handleDeleteFile(file.name)"
                :loading="isDeletingFile === file.name"
              >
                删除
              </el-button>
            </div>
          </div>
          <el-empty v-else description="暂无上传文件" />
        </div>
      </el-card>
      
      <el-alert
        v-if="errorMessage"
        :title="errorMessage"
        type="error"
        :closable="true"
        style="margin-bottom: 20px"
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
    
    <!-- 创建索引对话框 -->
    <el-dialog
      v-model="showCreateDialog"
      title="创建新索引"
      width="500px"
      :close-on-click-modal="false"
    >
      <el-form :model="createForm" :rules="createRules" ref="createFormRef" label-width="100px">
        <el-form-item label="索引名称" prop="name">
          <el-input
            v-model="createForm.name"
            placeholder="请输入索引名称"
            maxlength="100"
            show-word-limit
          />
          <div class="form-tip">
            只能包含字母、数字、下划线和连字符
          </div>
        </el-form-item>
        
        <el-form-item label="描述" prop="description">
          <el-input
            v-model="createForm.description"
            type="textarea"
            :rows="3"
            placeholder="请输入索引描述（可选）"
            maxlength="500"
            show-word-limit
          />
        </el-form-item>
      </el-form>
      
      <template #footer>
        <el-button @click="showCreateDialog = false">取消</el-button>
        <el-button type="primary" :loading="isCreating" @click="handleCreateIndex">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, computed } from 'vue'
import { ragAPI } from '../api/client'
import { ElMessage, ElMessageBox } from 'element-plus'

// 状态变量
const isLoading = ref(false)
const isLoadingIndexes = ref(false)
const isUploading = ref(false)
const isCreating = ref(false)
const isDeletingIndex = ref(false)
const isLoadingFiles = ref(false)
const isDeletingFile = ref('')
const result = ref(null)
const errorMessage = ref('')
const availableIndexes = ref([])
const files = ref([])
const uploadRef = ref(null)
const createFormRef = ref(null)
const selectedFile = ref(null)
const showCreateDialog = ref(false)

// 当前选中的索引名称
const selectedIndexName = ref('')

// 计算当前选中的索引对象
const selectedIndex = computed(() => {
    return availableIndexes.value.find(index => index.name === selectedIndexName.value) || { num_documents: 0 }
})

// 查询表单
const queryForm = reactive({
    query: '',
    k: 4,
    use_rag_agent: true,
})

// 创建索引表单
const createForm = reactive({
  name: '',
  description: '',
})

// 创建索引表单验证规则
const createRules = {
  name: [
    { required: true, message: '请输入索引名称', trigger: 'blur' },
    { 
      pattern: /^[a-zA-Z0-9_-]+$/, 
      message: '索引名称只能包含字母、数字、下划线和连字符', 
      trigger: 'blur' 
    },
    { min: 1, max: 100, message: '长度在 1 到 100 个字符', trigger: 'blur' }
  ],
  description: [
    { max: 500, message: '描述不能超过 500 个字符', trigger: 'blur' }
  ]
}

// 处理索引选择变化
const handleIndexChange = (value) => {
  if (value) {
    queryForm.index_name = value
    loadFiles()
  } else {
    files.value = []
  }
}

// 加载文件列表
const loadFiles = async () => {
  if (!selectedIndexName.value) return
  
  isLoadingFiles.value = true
  try {
    const response = await ragAPI.getDocuments(selectedIndexName.value)
    const data = response.data
    
    if (data.code === 200 && data.data) {
      files.value = data.data.files || []
    } else if (data.files) {
      files.value = data.files
    } else if (Array.isArray(data)) {
      files.value = data
    } else {
      files.value = []
    }
  } catch (error) {
    console.error('获取文件列表失败:', error)
    ElMessage.error('获取文件列表失败')
    files.value = []
  } finally {
    isLoadingFiles.value = false
  }
}

// 格式化文件大小
const formatFileSize = (bytes) => {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i]
}

// 格式化日期
const formatDate = (dateStr) => {
  if (!dateStr) return ''
  const date = new Date(dateStr)
  return date.toLocaleString('zh-CN')
}

// 处理删除文件
const handleDeleteFile = async (filename) => {
  try {
    await ElMessageBox.confirm(
      `确定要删除文件 "${filename}" 吗？`,
      '提示',
      {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning',
      }
    )
    
    isDeletingFile.value = filename
    await ragAPI.deleteDocument(selectedIndexName.value, filename)
    ElMessage.success('文件删除成功')
    await loadFiles()
    await fetchIndexes()
  } catch (error) {
    if (error !== 'cancel') {
      console.error('删除文件失败:', error)
      ElMessage.error('删除文件失败')
    }
  } finally {
    isDeletingFile.value = ''
  }
}

// 处理删除索引
const handleDeleteIndex = async () => {
  try {
    await ElMessageBox.confirm(
      `确定要删除索引 "${selectedIndexName.value}" 吗？此操作不可恢复！`,
      '警告',
      {
        confirmButtonText: '确定删除',
        cancelButtonText: '取消',
        type: 'warning',
      }
    )
    
    isDeletingIndex.value = true
    await ragAPI.deleteIndex(selectedIndexName.value)
    ElMessage.success('索引删除成功')
    selectedIndexName.value = ''
    files.value = []
    await fetchIndexes()
  } catch (error) {
    if (error !== 'cancel') {
      console.error('删除索引失败:', error)
      ElMessage.error('删除索引失败')
    }
  } finally {
    isDeletingIndex.value = false
  }
}

// 获取索引列表
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
    } else if (Array.isArray(data)) {
      indexes = data
    }
    
    availableIndexes.value = indexes.map(index => ({
      name: index.name,
      description: index.description || '',
      num_documents: index.num_documents || 0,
      created_at: index.created_at,
      updated_at: index.updated_at
    })).filter(index => index.name)
    
    // 如果有可用索引且当前没有选中，则选中第一个
    if (availableIndexes.value.length > 0 && !selectedIndexName.value) {
      selectedIndexName.value = availableIndexes.value[0].name
      queryForm.index_name = availableIndexes.value[0].name
    }
  } catch (error) {
    console.error('获取索引列表失败:', error)
    ElMessage.error('获取索引列表失败')
  } finally {
    isLoadingIndexes.value = false
  }
}

// 执行查询
const executeQuery = async () => {
  // 验证表单
  if (!selectedIndexName.value) {
    ElMessage.warning('请选择一个索引')
    return
  }
  
  if (selectedIndex.value.num_documents === 0) {
    ElMessage.warning('此索引暂无文档，请先上传文档后再查询')
    return
  }
  
  if (!queryForm.query || queryForm.query.trim() === '') {
    ElMessage.warning('请输入查询内容')
    return
  }
  
  if (queryForm.k < 1 || queryForm.k > 10) {
    ElMessage.warning('返回结果数应在 1-10 之间')
    return
  }
  
  isLoading.value = true
  result.value = null
  errorMessage.value = ''

  try {
    const response = await ragAPI.query({
      index_name: selectedIndexName.value,
      query: queryForm.query,
      k: queryForm.k,
      use_rag_agent: queryForm.use_rag_agent,
      return_sources: true
    })
    
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

// 处理文件选择
const handleFileChange = (file) => {
  selectedFile.value = file.raw
}

// 清除上传
const clearUpload = () => {
  selectedFile.value = null
  if (uploadRef.value) {
    uploadRef.value.clearFiles()
  }
}

// 上传文件
const handleUpload = async () => {
  if (!selectedFile.value) {
    ElMessage.warning('请先选择文件')
    return
  }
  if (!selectedIndexName.value) {
    ElMessage.warning('请先选择索引')
    return
  }

  isUploading.value = true
  try {
    await ragAPI.uploadDocument(selectedIndexName.value, selectedFile.value)
    ElMessage.success('文件上传并索引成功！')
    clearUpload()
    // 刷新索引列表和文件列表
    await fetchIndexes()
    await loadFiles()
  } catch (error) {
    console.error('上传失败:', error)
    let errorMsg = '上传失败'
    
    if (error.response) {
      if (error.response.status === 404) {
        errorMsg = '索引不存在'
      } else if (error.response.data && error.response.data.message) {
        errorMsg = error.response.data.message
      } else if (error.response.data && error.response.data.detail) {
        errorMsg = error.response.data.detail
      }
    }
    
    ElMessage.error(errorMsg)
  } finally {
    isUploading.value = false
  }
}

// 处理创建索引
const handleCreateIndex = async () => {
  if (!createFormRef.value) return
  
  try {
    await createFormRef.value.validate()
  } catch (error) {
    return
  }
  
  // 检查索引名称是否已存在
  const isIndexExists = availableIndexes.value.some(index => index.name === createForm.name)
  if (isIndexExists) {
    try {
      await ElMessageBox.confirm(
        `索引 "${createForm.name}" 已存在，是否覆盖？`,
        '提示',
        {
          confirmButtonText: '覆盖',
          cancelButtonText: '取消',
          type: 'warning',
        }
      )
    } catch {
      // 用户取消
      return
    }
  }
  
  isCreating.value = true
  try {
    await ragAPI.createEmptyIndex({
      name: createForm.name,
      description: createForm.description,
      overwrite: isIndexExists
    })
    
    ElMessage.success(`索引 "${createForm.name}" 创建成功！`)
    showCreateDialog.value = false
    
    // 重置表单
    createForm.name = ''
    createForm.description = ''
    
    // 刷新索引列表并选中新索引
    await fetchIndexes()
    selectedIndexName.value = createForm.name
    queryForm.index_name = createForm.name
    // 加载文件列表
    await loadFiles()
  } catch (error) {
    console.error('创建索引失败:', error)
    let errorMsg = '创建索引失败'
    
    if (error.response) {
      if (error.response.status === 409) {
        errorMsg = error.response.data.message || '索引已存在'
      } else if (error.response.data && error.response.data.message) {
        errorMsg = error.response.data.message
      } else if (error.response.data && error.response.data.detail) {
        errorMsg = error.response.data.detail
      }
    }
    
    ElMessage.error(errorMsg)
  } finally {
    isCreating.value = false
  }
}

// 初始化
onMounted(async () => {
  await fetchIndexes()
  if (selectedIndexName.value) {
    await loadFiles()
  }
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

.index-selector {
  display: flex;
  align-items: center;
  width: 100%;
}

.index-option {
  display: flex;
  align-items: center;
  width: 100%;
}

.index-name {
  font-weight: 500;
}

.index-count {
  color: #909399;
  font-size: 12px;
}

.form-tip {
  color: #909399;
  font-size: 12px;
  margin-top: 4px;
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

.files-section {
  margin-top: 20px;
}

.files-title {
  margin: 0 0 16px 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--el-text-color-primary);
  display: flex;
  align-items: center;
}

.files-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.file-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px;
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
  background: var(--el-fill-color-lighter);
}

.file-info {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.file-name {
  font-weight: 500;
  color: var(--el-text-color-primary);
}

.file-meta {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
