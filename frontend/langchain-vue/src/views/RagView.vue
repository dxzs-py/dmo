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
                  :label="`${index.name} (${index.chunkCount} 分段)`"
                  :value="index.name"
                >
                  <div class="index-option">
                    <span class="index-name">{{ index.name }}</span>
                    <el-tag v-if="index.description" type="info" size="small" style="margin-left: 10px">
                      {{ index.description }}
                    </el-tag>
                    <el-tag
                      v-if="index.chunkCount === 0"
                      type="warning"
                      size="small"
                      style="margin-left: auto"
                    >
                      空索引
                    </el-tag>
                    <span v-else class="index-count" style="margin-left: auto">{{ index.chunkCount }} 分段</span>
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
                :disabled="!selectedIndexName"
                :loading="isDeletingIndex"
                @click="handleDeleteIndex"
              >
                删除索引
              </el-button>
            </div>
            <div v-if="selectedIndexName && selectedIndex.chunkCount === 0" class="empty-index-hint">
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
              @keydown.enter.ctrl="executeQuery"
            />
          </el-form-item>
          
          <el-form-item label="返回结果数">
            <el-input-number v-model="queryForm.k" :min="1" :max="10" />
          </el-form-item>

          <el-form-item label="流式输出">
            <el-switch v-model="queryForm.streaming" />
            <span class="switch-hint">开启后实时逐字显示回答内容</span>
          </el-form-item>
          
          <el-form-item>
            <div class="query-actions">
              <el-button 
                type="primary" 
                :loading="isLoading && !isStreaming" 
                :disabled="!selectedIndexName || isStreaming" 
                @click="executeQuery"
              >
                {{ queryForm.streaming ? '流式查询' : '查询' }}
              </el-button>
              <el-button 
                v-if="isStreaming" 
                type="danger" 
                @click="stopStreaming"
              >
                停止生成
              </el-button>
              <el-button 
                :disabled="isLoading" 
                @click="clearResult"
              >
                清除结果
              </el-button>
            </div>
          </el-form-item>
        </el-form>
        
        <el-divider />
        
        <div class="upload-section">
          <h4 class="upload-title">上传文档到索引</h4>
          <el-upload
            ref="uploadRef"
            multiple
            drag
            :auto-upload="false"
            :on-change="handleFileChange"
            :file-list="uploadFiles"
            accept=".pdf,.txt,.md,.mdx,.html,.htm,.json,.csv,.docx,.xlsx,.pptx"
          >
            <el-icon :size="48"><Upload /></el-icon>
            <div>拖拽文件到此处或 <em>点击上传</em></div>
            <template #tip>
              <div class="upload-tip">支持 PDF、TXT、Markdown、MDX、HTML、JSON、CSV、DOCX、XLSX、PPTX 格式，支持多选上传</div>
            </template>
          </el-upload>
          <div v-if="uploadFiles.length > 0" class="upload-file-list">
            <div v-for="(file, idx) in uploadFiles" :key="idx" class="upload-file-item">
              <el-icon><Document /></el-icon>
              <span>{{ file.name }}</span>
              <span class="file-size">{{ formatFileSize(file.size) }}</span>
            </div>
          </div>
          <div class="upload-actions">
            <el-button
              type="success"
              :disabled="!uploadFiles.length || !selectedIndexName"
              :loading="isUploading"
              @click="handleUpload"
            >
              {{ isUploading ? '正在上传并索引...' : '上传并索引' }}
            </el-button>
            <el-button :disabled="!uploadFiles.length" @click="clearUpload">清除</el-button>
          </div>
          <div v-if="isUploading" class="upload-progress">
            <el-progress :percentage="uploadProgress" :status="uploadProgress >= 100 ? 'success' : undefined" />
            <div class="upload-status-text">{{ uploadStatusText }}</div>
          </div>
          <!-- UX-c：接收确认区列出后端已接收文件清单（files 字段消费） -->
          <div v-if="receivedFiles.length" class="received-files">
            <el-alert type="success" :closable="false" show-icon>
              <template #title>
                已接收 {{ receivedFiles.length }} 个文件：{{ receivedFiles.join('、') }}
              </template>
            </el-alert>
          </div>
        </div>

        <el-divider />

        <div v-if="selectedIndexName" class="files-section">
          <h4 class="files-title">
            已上传文件
            <!-- R-UX-02：消费分页 total，消除第 21 个文件起静默消失 -->
            <span v-if="filesTotal > 0" class="files-total-hint">共 {{ filesTotal }} 个</span>
            <el-button
              link
              :loading="isLoadingFiles"
              style="margin-left: 10px"
              @click="loadFiles"
            >
              刷新
            </el-button>
          </h4>
          <div v-if="files.length > 0" class="files-list">
            <div
              v-for="file in files" :key="file.name" v-memo="[file.name, file.size, file.uploadedAt, isDeletingFile === file.name]"
                 class="file-item">
              <div class="file-info">
                <span class="file-name">{{ file.name }}</span>
                <span class="file-meta">
                  {{ formatFileSize(file.size) }} · {{ formatDate(file.uploadedAt) }}
                </span>
              </div>
              <el-button
                type="danger"
                size="small"
                :loading="isDeletingFile === file.name"
                @click="handleDeleteFile(file.name)"
              >
                删除
              </el-button>
            </div>
          </div>
          <el-empty v-else-if="!isLoadingFiles" description="暂无上传文件" />
          <!-- R-UX-02：文件列表服务端分页 -->
          <div v-if="filesTotal > filesPageSize" class="files-pagination">
            <el-pagination
              layout="total, prev, pager, next"
              :total="filesTotal"
              :page-size="filesPageSize"
              :current-page="filesPage"
              @current-change="handleFilesPageChange"
            />
          </div>
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
            <el-tag v-if="isStreaming" type="warning" class="streaming-tag">
              {{ stageHint || '生成中...' }}
            </el-tag>
          </div>
        </template>

        <!-- UX-e：答案区走 MarkdownRenderer，包局部 ErrorBoundary 防渲染异常炸整页
             （research 已有局部边界先例） -->
        <ErrorBoundary>
          <div v-if="result.success !== false">
            <h4>答案</h4>
            <div class="answer-content">
              <!-- R-UX-03：等待期阶段占位（start/heartbeat 事件已由后端发出），
                   首 chunk 到达后清 stageHint 切回正文渲染 -->
              <div v-if="!streamingAnswer && stageHint" class="stage-hint">
                <span class="stage-hint-dot" />
                {{ stageHint }}
              </div>
              <MarkdownRenderer v-else :content="result.answer || streamingAnswer" />
            </div>

            <div v-if="result.sources && result.sources.length > 0">
              <h4>参考来源</h4>
              <el-collapse>
                <el-collapse-item
                  v-for="(source, index) in result.sources"
                  :key="index"
                  v-memo="[source.content, formatSourceTitle(source, index), source.degraded, source.score]"
                  :name="index"
                >
                  <template #title>
                    <span class="source-title">{{ formatSourceTitle(source, index) }}</span>
                    <!-- UX-a：来源级降级徽标（degradation 事件是全局 toast，此处标记单条来源） -->
                    <el-tag v-if="source.degraded" type="warning" size="small" class="source-flag-tag">
                      降级检索
                    </el-tag>
                    <!-- R-UX-04：相关度分数透出显示（后端检索内核归一相似度语义，越大越相关） -->
                    <el-tag v-if="typeof source.score === 'number'" type="info" size="small" class="source-flag-tag">
                      相关度: {{ source.score.toFixed(3) }}
                    </el-tag>
                  </template>
                  <p class="source-content">{{ source.content }}</p>
                </el-collapse-item>
              </el-collapse>
            </div>
          </div>

          <div v-else class="error">
            <el-alert type="error" :title="result.error || '查询失败'" />
          </div>
        </ErrorBoundary>
      </el-card>
    </div>
    
    <!-- 创建索引对话框 -->
    <el-dialog
      v-model="showCreateDialog"
      title="创建新索引"
      width="500px"
      :close-on-click-modal="false"
    >
      <el-form ref="createFormRef" :model="createForm" :rules="createRules" label-width="100px">
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
import { ref, reactive, onActivated, computed, onUnmounted, onDeactivated } from 'vue'
import { ragAPI } from '@/api/knowledge'
import { ElMessage } from 'element-plus'
import { Upload, Document } from '@element-plus/icons-vue'
import { formatDate, formatFileSize } from '../utils/format'
import MarkdownRenderer from '../components/common/MarkdownRenderer.vue'
import ErrorBoundary from '../components/common/ErrorBoundary.vue'
import { logger } from '../utils/logger'
import { extractErrorMessage } from '../utils/apiErrorHandler'
import { confirmDelete, confirmAction } from '../utils/dialog'
import { useSessionStore } from '../stores/session'
import { useRealtimeSync } from '../composables/useRealtimeSync'
import { useApiTask } from '../composables/useApiTask'
import { useSSEStream } from '../composables/useSSEStream'
import { buildUploadFormData, notifyEmbeddingFallback, getUploadErrorMessage, trackUploadTask, mapUploadProgress } from '../utils/knowledgeUpload'
import { formatSourceTitle } from '../utils/ragSources'

const sessionStore = useSessionStore()
// isDeletingFile（存文件名，标记删除中的行）与 isUploading（成功路径由 WS 回调复位）为手动管理
const isUploading = ref(false)
const isDeletingFile = ref('')
const isStreaming = ref(false)
const result = ref(null)
const errorMessage = ref('')
// R-UX-03：流式等待期阶段文案——后端 start("检索中...")与 heartbeat 三阶段
// （正在改写查询/正在检索文档/正在生成回答）此前前端零消费，首 chunk 前答案区空白
const stageHint = ref('')
const availableIndexes = ref([])
const files = ref([])
const uploadRef = ref(null)
const createFormRef = ref(null)
const uploadFiles = ref([])
const showCreateDialog = ref(false)
const streamingAnswer = ref('')
// SSE 连接骨架委托 useSSEStream（onEvent 惰性转发 handleStreamEvent，避免定义顺序 TDZ）
const sseStream = useSSEStream({ onEvent: (parsed) => handleStreamEvent(parsed) })

const selectedIndexName = ref('')

const selectedIndex = computed(() => {
  return availableIndexes.value.find(index => index.name === selectedIndexName.value) || { chunkCount: 0 }
})

const queryForm = reactive({
  query: '',
  k: 4,
  streaming: true,
  indexName: '',
})

const createForm = reactive({
  name: '',
  description: '',
})

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

const handleIndexChange = (value) => {
  if (value) {
    queryForm.indexName = value
    loadFiles()
  } else {
    files.value = []
  }
  receivedFiles.value = []
}

// R-UX-02：文件列表真分页（后端默认 20/页，消费 total 翻页）
const filesPage = ref(1)
const filesPageSize = ref(20)
const filesTotal = ref(0)

const { run: runLoadFiles, loading: isLoadingFiles } = useApiTask(
  async (page = 1) => {
    const response = await ragAPI.getDocuments(selectedIndexName.value, {
      page,
      page_size: filesPageSize.value,
    })
    const data = response.data

    let items = []
    let total = 0
    let currentPage = page
    if (data.code === 200 && data.data) {
      items = data.data.items || []
      total = data.data.total ?? items.length
      currentPage = data.data.page || page
    } else if (data.items) {
      items = data.items
      total = data.total ?? items.length
    } else if (Array.isArray(data)) {
      items = data
    }
    files.value = items
    filesTotal.value = total
    filesPage.value = currentPage
  },
  {
    showErrorToast: false,
    onError: (error) => {
      logger.error('获取文件列表失败:', error)
      ElMessage.error('获取文件列表失败: ' + extractErrorMessage(error))
      files.value = []
      filesTotal.value = 0
    },
  }
)

const loadFiles = (page = 1) => {
  if (!selectedIndexName.value) return
  return runLoadFiles(page)
}

const handleFilesPageChange = (page) => {
  loadFiles(page)
}

const { run: runDeleteFile } = useApiTask(
  async (filename) => {
    await ragAPI.deleteDocument(selectedIndexName.value, filename)
    ElMessage.success('文件删除成功')
    // 当前页删空时回退一页（分页配套）
    const lastPage = Math.max(1, Math.ceil((filesTotal.value - 1) / filesPageSize.value))
    const targetPage = files.value.length === 1 ? Math.min(filesPage.value - 1, lastPage) : filesPage.value
    await loadFiles(Math.max(1, targetPage))
    await fetchIndexes()
  },
  {
    loading: false,
    showErrorToast: false,
    onError: (error) => {
      logger.error('删除文件失败:', error)
      ElMessage.error('删除文件失败: ' + extractErrorMessage(error))
    },
  }
)

const handleDeleteFile = async (filename) => {
  try {
    await confirmDelete(`确定要删除文件 "${filename}" 吗？`)
  } catch {
    return
  }
  isDeletingFile.value = filename
  try {
    await runDeleteFile(filename)
  } finally {
    isDeletingFile.value = ''
  }
}

const { run: runDeleteIndex, loading: isDeletingIndex } = useApiTask(
  async () => {
    await ragAPI.deleteIndex(selectedIndexName.value)
    ElMessage.success('索引删除成功')
    selectedIndexName.value = ''
    files.value = []
    await fetchIndexes()
    await sessionStore.loadKnowledgeBases()
  },
  {
    showErrorToast: false,
    onError: (error) => {
      logger.error('删除索引失败:', error)
      ElMessage.error('删除索引失败: ' + extractErrorMessage(error))
    },
  }
)

const handleDeleteIndex = async () => {
  try {
    await confirmDelete(`确定要删除索引 "${selectedIndexName.value}" 吗？此操作不可恢复！`, { confirmButtonText: '确定删除' })
  } catch {
    return
  }
  await runDeleteIndex()
}

const { run: runFetchIndexes, loading: isLoadingIndexes } = useApiTask(
  async () => {
    // R-UX-02：索引下拉消费分页契约——page_size=100 一次拉取（上限），超限显式警告非静默截断
    const response = await ragAPI.getIndices({ page_size: 100 })
    const data = response.data

    let indexes = []
    if (data.code === 200 && data.data) {
      indexes = Array.isArray(data.data) ? data.data : data.data.items || []
      const total = Array.isArray(data.data) ? indexes.length : data.data.total ?? indexes.length
      if (total > 100) {
        ElMessage.warning(`索引数量（${total}）超过单页上限，仅显示前 100 个`)
      }
    } else if (data.indexes) {
      indexes = data.indexes
    } else if (Array.isArray(data)) {
      indexes = data
    }

    availableIndexes.value = indexes.map(index => ({
      name: index.name,
      description: index.description || '',
      chunkCount: index.chunkCount || 0,
      createdAt: index.createdAt,
      updatedAt: index.updatedAt
    })).filter(index => index.name)

    if (availableIndexes.value.length > 0 && !selectedIndexName.value) {
      selectedIndexName.value = availableIndexes.value[0].name
      queryForm.indexName = availableIndexes.value[0].name
    }
  },
  {
    showErrorToast: false,
    onError: (error) => {
      logger.error('获取索引列表失败:', error)
      ElMessage.error('获取索引列表失败: ' + extractErrorMessage(error))
    },
  }
)

const fetchIndexes = () => runFetchIndexes()

const validateQuery = () => {
  if (!selectedIndexName.value) {
    ElMessage.warning('请选择一个索引')
    return false
  }
  
  if (selectedIndex.value.chunkCount === 0) {
    ElMessage.warning('此索引暂无文档，请先上传文档后再查询')
    return false
  }
  
  if (!queryForm.query || queryForm.query.trim() === '') {
    ElMessage.warning('请输入查询内容')
    return false
  }
  
  if (queryForm.k < 1 || queryForm.k > 10) {
    ElMessage.warning('返回结果数应在 1-10 之间')
    return false
  }
  
  return true
}

const executeQuery = async () => {
  if (!validateQuery()) return

  if (queryForm.streaming) {
    await executeStreamQuery()
  } else {
    await executeNormalQuery()
  }
}

// isLoading 与 executeStreamQuery 共用同一 ref：普通查询由 useApiTask 托管，
// 流式路径（维持原逻辑不迁）仍手动置位，二者不并发
const { run: runNormalQuery, loading: isLoading } = useApiTask(
  () => ragAPI.query({
    indexName: selectedIndexName.value,
    query: queryForm.query,
    k: queryForm.k,
    returnSources: true
  }),
  {
    showErrorToast: false,
    onSuccess: (response) => {
      result.value = response.data.data || response.data
      ElMessage.success('查询成功')
    },
    onError: (error) => {
      logger.error('查询失败:', error)
      // R-UX-05：统一错误文案入口（覆盖 status 分类/后端 message/detail/网络错误）
      const errorMsg = extractErrorMessage(error)
      errorMessage.value = errorMsg
      ElMessage.error(errorMsg)
      result.value = { success: false, error: errorMsg }
    },
  }
)

const executeNormalQuery = async () => {
  result.value = null
  errorMessage.value = ''
  streamingAnswer.value = ''
  await runNormalQuery()
}

const executeStreamQuery = async () => {
  isLoading.value = true
  isStreaming.value = true
  result.value = { answer: '', sources: [] }
  errorMessage.value = ''
  streamingAnswer.value = ''
  stageHint.value = ''

  try {
    const streamResult = await sseStream.start((signal) =>
      ragAPI.streamQuery({
        indexName: selectedIndexName.value,
        query: queryForm.query,
        k: queryForm.k,
        returnSources: true
      }, { signal })
    )

    if (streamResult.status === 'completed') {
      ElMessage.success('查询完成')
    } else if (streamResult.status === 'aborted') {
      ElMessage.info('已停止生成')
    } else {
      logger.error('流式查询失败:', streamResult.error)
      const errorMsg = streamResult.error?.message || '流式查询失败'
      errorMessage.value = errorMsg
      ElMessage.error(errorMsg)
      if (!result.value || !streamingAnswer.value) {
        result.value = { success: false, error: errorMsg }
      }
    }
  } finally {
    isLoading.value = false
    isStreaming.value = false
    stageHint.value = ''
    if (result.value && streamingAnswer.value) {
      result.value.answer = streamingAnswer.value
    }
  }
}

const handleStreamEvent = (convertedData) => {
  // 命名边界：readSSEStream 已统一调用 parseProtocolEvent 完成转换
  // （toCamelCase + type 还原为后端原始 snake_case 协议路由标识符），
  // 消费方只拿已转换对象，case 分支字段访问均为 camelCase。
  switch (convertedData.type) {
    // R-UX-03：start（"检索中..."）与 heartbeat（改写查询/检索文档/生成回答三阶段）
    // 事件此前零消费——渲染为答案区占位文案，首 chunk 到达后清除
    case 'start':
      stageHint.value = convertedData.message || '检索中...'
      break
    case 'heartbeat':
      if (convertedData.message) {
        stageHint.value = convertedData.message
      }
      break
    case 'chunk':
      stageHint.value = ''
      if (convertedData.content) {
        streamingAnswer.value += convertedData.content
        if (result.value) {
          result.value.answer = streamingAnswer.value
        }
      }
      break
    case 'end':
      stageHint.value = ''
      if (result.value) {
        result.value.answer = streamingAnswer.value
        result.value.success = true
      }
      break
    case 'error':
      stageHint.value = ''
      errorMessage.value = convertedData.message || convertedData.error || '查询出错'
      if (result.value) {
        result.value.success = false
        result.value.error = errorMessage.value
      }
      break
    case 'sources':
      if (convertedData.data && result.value) {
        result.value.sources = convertedData.data
      }
      break
    case 'degradation':
      if (convertedData.message) {
        ElMessage.warning({ message: convertedData.message, duration: 5000 })
      }
      break
    default:
      if (convertedData.content) {
        streamingAnswer.value += convertedData.content
        if (result.value) {
          result.value.answer = streamingAnswer.value
        }
      }
  }
}

const stopStreaming = () => {
  sseStream.abort()
  isStreaming.value = false
}

const clearResult = () => {
  result.value = null
  errorMessage.value = ''
  streamingAnswer.value = ''
  stageHint.value = ''
}

const handleFileChange = (file, fileList) => {
  uploadFiles.value = fileList
}

const clearUpload = () => {
  uploadFiles.value = []
  receivedFiles.value = []
  if (uploadRef.value) {
    uploadRef.value.clearFiles()
  }
}

const uploadProgress = ref(0)
const uploadStatusText = ref('')
// UX-c：上传响应 files 字段——接收确认区列出后端已接收文件清单
const receivedFiles = ref([])

const realtimeSync = useRealtimeSync()
let cancelUploadTask = null

// 成功路径 isUploading 由 trackUploadTask 的 WS 终态回调复位（非 finally），
// 故 loading 不托管；onSuccess 仅衔接 WS 订阅，toast 留在 WS 回调内
const { run: runUpload } = useApiTask(
  async () => {
    const formData = buildUploadFormData(uploadFiles.value)
    // R-UX-06：HTTP 上传段经 onUploadProgress 映射到 0-30 段，消除首段盲窗
    const response = await ragAPI.uploadDocuments(selectedIndexName.value, formData, {
      onUploadProgress: (event) => {
        const pct = mapUploadProgress(event.loaded, event.total)
        if (pct > 0) {
          uploadProgress.value = Math.max(uploadProgress.value, pct)
          uploadStatusText.value = `正在上传文件... ${pct}%`
        }
      },
    })
    const taskId = response.data?.data?.taskId
    if (!taskId) {
      throw new Error('上传响应缺少任务ID')
    }
    // UX-c：files 为后端已接收文件清单（保存成功即返回，先于向量化终态）
    return { taskId, files: (response.data?.data?.files || []).map(f => f.name || f.fileName).filter(Boolean) }
  },
  {
    loading: false,
    showErrorToast: false,
    onSuccess: ({ taskId, files: received }) => {
      receivedFiles.value = received
      uploadStatusText.value = '文档已接收，正在后台处理...'
      cancelUploadTask = trackUploadTask(realtimeSync, taskId, {
        onProgress: (progress, step) => {
          uploadProgress.value = progress
          uploadStatusText.value = step || `处理中 ${progress}%`
        },
        onSuccess: (result) => {
          isUploading.value = false
          uploadProgress.value = 100
          uploadStatusText.value = ''
          ElMessage.success('文件上传并索引成功！')
          notifyEmbeddingFallback(result)
          clearUpload()
          fetchIndexes()
          loadFiles()
        },
        onFailure: (errorMsg) => {
          isUploading.value = false
          uploadProgress.value = 0
          uploadStatusText.value = ''
          ElMessage.error(errorMsg || '上传处理失败')
        },
      })
    },
    onError: (error) => {
      logger.error('上传失败:', error)
      ElMessage.error(getUploadErrorMessage(error))
      isUploading.value = false
      uploadProgress.value = 0
      uploadStatusText.value = ''
    },
  }
)

const handleUpload = async () => {
  if (!uploadFiles.value.length) {
    ElMessage.warning('请先选择文件')
    return
  }
  if (!selectedIndexName.value) {
    ElMessage.warning('请先选择索引')
    return
  }

  isUploading.value = true
  uploadProgress.value = 0
  uploadStatusText.value = '正在上传文件...'
  receivedFiles.value = []
  await runUpload()
}

const { run: runCreateIndex, loading: isCreating } = useApiTask(
  async (isIndexExists) => {
    await ragAPI.createIndex({
      name: createForm.name,
      description: createForm.description,
      overwrite: isIndexExists
    })

    // 先捕获索引名再清空表单（修复：清空后再取 createForm.name 恒为空，
    // 导致新索引永不选中、文件列表不加载）
    const createdName = createForm.name
    ElMessage.success(`索引 "${createdName}" 创建成功！`)
    showCreateDialog.value = false

    createForm.name = ''
    createForm.description = ''

    await fetchIndexes()
    selectedIndexName.value = createdName
    queryForm.indexName = createdName
    await loadFiles()
  },
  {
    showErrorToast: false,
    onError: (error) => {
      logger.error('创建索引失败:', error)
      // R-UX-05：统一错误文案入口（409 冲突等后端 message 优先透出）
      ElMessage.error(extractErrorMessage(error))
    },
  }
)

const handleCreateIndex = async () => {
  if (!createFormRef.value) return

  try {
    await createFormRef.value.validate()
  } catch {
    return
  }

  const isIndexExists = availableIndexes.value.some(index => index.name === createForm.name)
  if (isIndexExists) {
    try {
      await confirmAction(
        `索引 "${createForm.name}" 已存在，是否覆盖？`,
        { confirmButtonText: '覆盖', type: 'warning' }
      )
    } catch {
      return
    }
  }

  await runCreateIndex(isIndexExists)
}

// keep-alive 首挂 mounted→activated 依次触发，初始化统一收敛 onActivated 单一入口
// （此前双钩子同帧各调一次 fetchIndexes，indices 接口双发）；
// loadFiles 仅首挂需要（fetchIndexes 首次确定选中索引后加载其文档），由首挂标志区分。
let isFirstActivate = true
onActivated(async () => {
  await fetchIndexes()
  if (isFirstActivate) {
    isFirstActivate = false
    if (selectedIndexName.value) {
      await loadFiles()
    }
  }
})

// R-UX-06：keep-alive 停用不取消上传任务订阅——WS 回调持续更新响应式状态，
// 回到页面即见终态（任务后台继续执行，replay 机制兜底历史帧）；订阅仅随组件销毁释放
const cleanupRagView = () => {
  stopStreaming()
}
const disposeRagView = () => {
  cleanupRagView()
  if (cancelUploadTask) {
    cancelUploadTask()
    cancelUploadTask = null
  }
}
onUnmounted(disposeRagView)
// keep-alive 缓存生效后组件停用不卸载（onUnmounted 不触发），停用时仅断开流式查询
onDeactivated(cleanupRagView)
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

.switch-hint {
  margin-left: 12px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.query-actions {
  display: flex;
  gap: 12px;
  align-items: center;
}

.query-card {
  margin-bottom: 20px;
}

.result-card {
  margin-top: 20px;
}

.streaming-tag {
  animation: pulse-opacity 1.5s ease-in-out infinite;
}

@keyframes pulse-opacity {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

.answer-content {
  background: var(--el-fill-color-lighter);
  padding: 16px;
  border-radius: 6px;
  line-height: 1.8;
  min-height: 60px;
}

/* R-UX-03：等待期阶段占位文案（脉冲呼吸） */
.stage-hint {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--el-text-color-secondary);
  font-size: 14px;
  animation: stage-hint-pulse 1.5s ease-in-out infinite;
}

.stage-hint-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--el-color-warning);
  flex-shrink: 0;
}

@keyframes stage-hint-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.55; }
}

/* 来源面板：标题行内联徽标（UX-a 降级标记 / R-UX-04 相关度分数） */
.source-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.source-flag-tag {
  margin-left: 8px;
  flex-shrink: 0;
}

.received-files {
  margin-top: 12px;
}

.files-total-hint {
  margin-left: 10px;
  font-size: 12px;
  font-weight: 400;
  color: var(--el-text-color-secondary);
}

.files-pagination {
  margin-top: 12px;
  display: flex;
  justify-content: flex-end;
}

.source-content {
  white-space: pre-wrap;
  line-height: 1.6;
  color: var(--el-text-color-regular);
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

.upload-progress {
  margin-top: 12px;
  padding: 10px 12px;
  background: var(--el-fill-color-light);
  border-radius: 6px;
}

.upload-status-text {
  margin-top: 6px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  text-align: center;
}

.upload-tip {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.upload-file-list {
  margin-top: 16px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 8px;
  max-height: 200px;
  overflow-y: auto;
}

.upload-file-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 4px;
  background: var(--el-fill-color-lighter);
  margin-bottom: 4px;
  font-size: 13px;
}

.upload-file-item:last-child {
  margin-bottom: 0;
}

.file-size {
  margin-left: auto;
  color: var(--el-text-color-secondary);
  font-size: 12px;
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

@media (max-width: 1024px) {
  .rag-view {
    padding: 16px;
  }

  .view-content {
    max-width: 100%;
  }
}

@media (max-width: 768px) {
  .rag-view {
    padding: 12px;
  }

  .index-selector {
    flex-wrap: wrap;
  }

  .query-actions {
    flex-wrap: wrap;
  }

  .page-title {
    font-size: 16px;
  }
}

@media (max-width: 480px) {
  .rag-view {
    padding: 8px;
  }

  .page-title {
    font-size: 14px;
  }

  .index-selector {
    flex-direction: column;
    gap: 8px;
  }

  .answer-content {
    padding: 12px;
    font-size: 13px;
  }

  .file-item {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }
}
</style>
