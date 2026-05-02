<script setup>
import { ref, onMounted, onUnmounted, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus, Search, Upload, Delete, View, Document, FolderOpened, Edit, Refresh, Download } from '@element-plus/icons-vue'
import { knowledgeAPI } from '../api'
import { logger } from '../utils/logger'
import { formatFileSize } from '../utils/format'
import { confirmDelete, confirmAction } from '../utils/dialog'

const knowledgeBases = ref([])
const loading = ref(false)
const searchQuery = ref('')
const createDialog = ref(false)
const detailDialog = ref(false)
const uploadDialog = ref(false)
const testDialog = ref(false)
const documentsDialog = ref(false)
const editDialog = ref(false)

const newKB = ref({ name: '', description: '' })
const currentKB = ref(null)
const currentKBEdit = ref({ id: '', name: '', description: '' })
const uploadFiles = ref([])
const testQuery = ref('')
const testResults = ref([])
const testLoading = ref(false)
const documents = ref([])
const documentsLoading = ref(false)

const cacheStats = ref(null)
const cacheHealth = ref(null)
const cacheLoading = ref(false)
const cacheClearLoading = ref(false)
const cacheAutoRefresh = ref(false)
const mysqlStatus = ref(null)
const vectorStoreStatus = ref(null)
let cacheTimer = null

const filteredKBs = computed(() => {
  if (!searchQuery.value) return knowledgeBases.value
  const q = searchQuery.value.toLowerCase()
  return knowledgeBases.value.filter(
    kb => kb.name.toLowerCase().includes(q) || kb.description?.toLowerCase().includes(q)
  )
})

onMounted(async () => {
  await loadKnowledgeBases()
  await loadCacheInfo()
})

onUnmounted(() => {
  if (cacheTimer) {
    clearInterval(cacheTimer)
    cacheTimer = null
  }
})

function toggleAutoRefresh(val) {
  if (cacheTimer) {
    clearInterval(cacheTimer)
    cacheTimer = null
  }
  if (val) {
    cacheTimer = setInterval(loadCacheInfo, 10000)
  }
}

async function loadKnowledgeBases() {
  loading.value = true
  try {
    const response = await knowledgeAPI.getKnowledgeBases()
    if (response.data?.code === 200) {
      knowledgeBases.value = response.data.data?.items || []
    }
  } catch (error) {
    logger.error('加载知识库失败:', error)
  } finally {
    loading.value = false
  }
}

async function handleCreate() {
  if (!newKB.value.name) {
    ElMessage.warning('请输入知识库名称')
    return
  }
  try {
    const response = await knowledgeAPI.createKnowledgeBase(newKB.value)
    if (response.data?.code === 200) {
      ElMessage.success('知识库创建成功')
      createDialog.value = false
      newKB.value = { name: '', description: '' }
      await loadKnowledgeBases()
    }
  } catch (error) {
    ElMessage.error('创建失败')
  }
}

async function handleDelete(kb) {
  try {
    await confirmDelete(`确定要删除知识库 "${kb.name}" 吗？此操作不可恢复。`)
    const response = await knowledgeAPI.deleteKnowledgeBase(kb.id)
    if (response.data?.code === 200) {
      ElMessage.success('删除成功')
      await loadKnowledgeBases()
    }
  } catch {
    // cancelled
  }
}

async function openDetail(kb) {
  currentKB.value = kb
  try {
    const response = await knowledgeAPI.getKnowledgeBaseDetail(kb.id)
    if (response.data?.code === 200) {
      currentKB.value = response.data.data
    }
  } catch (error) {
    logger.error('获取详情失败:', error)
  }
  detailDialog.value = true
}

function openEdit(kb) {
  currentKBEdit.value = {
    id: kb.id,
    name: kb.name,
    description: kb.description,
  }
  editDialog.value = true
}

async function handleEdit() {
  try {
    const response = await knowledgeAPI.updateKnowledgeBase(currentKBEdit.value.id, {
      description: currentKBEdit.value.description,
    })
    if (response.data?.code === 200) {
      ElMessage.success('更新成功')
      editDialog.value = false
      await loadKnowledgeBases()
    }
  } catch (error) {
    ElMessage.error('更新失败')
  }
}

function openUpload(kb) {
  currentKB.value = kb
  uploadFiles.value = []
  uploadDialog.value = true
}

const uploadLoading = ref(false)

async function handleUpload() {
  if (!uploadFiles.value.length) {
    ElMessage.warning('请选择文件')
    return
  }
  const formData = new FormData()
  uploadFiles.value.forEach(file => {
    formData.append('files', file.raw)
  })
  uploadLoading.value = true
  try {
    const response = await knowledgeAPI.uploadDocuments(currentKB.value.id, formData)
    if (response.data?.code === 200) {
      ElMessage.success('文档上传并向量化成功！')
      uploadDialog.value = false
      await loadKnowledgeBases()
    }
  } catch (error) {
    logger.error('上传失败:', error)
    ElMessage.error('上传失败，请检查文件格式或重试')
  } finally {
    uploadLoading.value = false
  }
}

async function openDocuments(kb) {
  currentKB.value = kb
  documents.value = []
  documentsLoading.value = true
  try {
    const response = await knowledgeAPI.getDocuments(kb.id)
    if (response.data?.code === 200) {
      documents.value = response.data.data?.files || []
      logger.info('获取文档列表成功:', documents.value)
    } else {
      ElMessage.error('获取文档列表失败: ' + (response.data?.message || '未知错误'))
    }
  } catch (error) {
    logger.error('获取文档列表失败:', error)
    ElMessage.error('获取文档列表失败，请重试')
  } finally {
    documentsLoading.value = false
  }
  documentsDialog.value = true
}

async function handleDeleteDocument(kbId, filename) {
  try {
    await confirmDelete(`确定要删除文档 "${filename}" 吗？`)
    
    logger.info('正在删除文档:', filename)
    const response = await knowledgeAPI.deleteDocument(kbId, filename)
    logger.info('删除文档响应:', response.data)
    
    if (response.data?.code === 200) {
      ElMessage.success('删除成功')
      
      // 立即从本地列表中移除，确保用户看到立即的反馈
      const initialLength = documents.value.length
      documents.value = documents.value.filter(d => d.name !== filename)
      logger.info(`从列表中移除文件: ${filename}, 之前: ${initialLength}, 之后: ${documents.value.length}`)
      
      // 稍等一下后再刷新，确保后端处理完成
      setTimeout(async () => {
        logger.info('重新加载文档列表...')
        await openDocuments(currentKB.value)
        await loadKnowledgeBases()
        logger.info('文档列表刷新完成')
      }, 300)
    } else {
      ElMessage.error('删除失败: ' + (response.data?.message || '未知错误'))
    }
  } catch (error) {
    if (error !== 'cancel') {
      logger.error('删除文档失败:', error)
      ElMessage.error('删除失败，请重试')
    }
  }
}

function openTest(kb) {
  currentKB.value = kb
  testQuery.value = ''
  testResults.value = []
  testDialog.value = true
}

async function handleTestSearch() {
  if (!testQuery.value) return
  testLoading.value = true
  try {
    const response = await knowledgeAPI.testSearch(currentKB.value.id, {
      query: testQuery.value,
      top_k: 5,
    })
    if (response.data?.code === 200) {
      testResults.value = response.data.data?.results || []
    }
  } catch (error) {
    ElMessage.error('检索测试失败')
  } finally {
    testLoading.value = false
  }
}

function handleFileChange(file, fileList) {
  uploadFiles.value = fileList
}

async function loadCacheInfo() {
  cacheLoading.value = true
  try {
    const [healthRes, statsRes, dbOverviewRes] = await Promise.allSettled([
      knowledgeAPI.getCacheHealth(),
      knowledgeAPI.getCacheStats(),
      knowledgeAPI.getDatabaseOverview(),
    ])
    if (healthRes.status === 'fulfilled' && healthRes.value.data?.code === 200) {
      cacheHealth.value = healthRes.value.data.data
    }
    if (statsRes.status === 'fulfilled' && statsRes.value.data?.code === 200) {
      cacheStats.value = statsRes.value.data.data
    }
    if (dbOverviewRes.status === 'fulfilled' && dbOverviewRes.value.data?.code === 200) {
      const overview = dbOverviewRes.value.data.data
      mysqlStatus.value = overview.mysql
      vectorStoreStatus.value = overview.vector_store
    }
  } catch (error) {
    logger.error('加载数据库信息失败:', error)
  } finally {
    cacheLoading.value = false
  }
}

async function handleClearCache(scope = 'all') {
  try {
    await confirmAction(
      scope === 'all' ? '确定要清除所有缓存吗？此操作不可恢复。' : `确定要清除 ${scope} 缓存吗？`,
      { confirmButtonText: '清除', type: 'warning' }
    )
    cacheClearLoading.value = true
    const response = await knowledgeAPI.clearCache({ scope })
    if (response.data?.code === 200) {
      ElMessage.success('缓存清除成功')
      await loadCacheInfo()
    }
  } catch {
    // cancelled
  } finally {
    cacheClearLoading.value = false
  }
}
</script>

<template>
  <div class="knowledge-container">
    <div class="knowledge-header">
      <h1>知识库管理</h1>
      <p class="subtitle">管理 RAG 检索增强生成的知识库和文档</p>
    </div>

    <div class="knowledge-toolbar">
      <el-input
        v-model="searchQuery"
        placeholder="搜索知识库..."
        :prefix-icon="Search"
        clearable
        class="search-input"
      />
      <el-button type="primary" :icon="Plus" @click="createDialog = true">
        创建知识库
      </el-button>
    </div>

    <el-row :gutter="20" v-loading="loading">
      <el-col :xs="24" :sm="12" :md="8" v-for="kb in filteredKBs" :key="kb.id"
              v-memo="[kb.name, kb.description, kb.document_count, kb.chunk_count, kb.updated_at]">
        <el-card class="kb-card" shadow="hover">
          <template #header>
            <div class="kb-card-header">
              <div class="kb-card-title">
                <el-icon><FolderOpened /></el-icon>
                <span>{{ kb.name }}</span>
              </div>
              <el-dropdown trigger="click">
                <el-button text size="small">···</el-button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item @click="openDetail(kb)">
                      <el-icon><View /></el-icon>查看详情
                    </el-dropdown-item>
                    <el-dropdown-item @click="openEdit(kb)">
                      <el-icon><Edit /></el-icon>编辑描述
                    </el-dropdown-item>
                    <el-dropdown-item @click="openUpload(kb)">
                      <el-icon><Upload /></el-icon>上传文档
                    </el-dropdown-item>
                    <el-dropdown-item @click="openDocuments(kb)">
                      <el-icon><Document /></el-icon>文档管理
                    </el-dropdown-item>
                    <el-dropdown-item @click="openTest(kb)">
                      <el-icon><Search /></el-icon>检索测试
                    </el-dropdown-item>
                    <el-dropdown-item divided @click="handleDelete(kb)">
                      <el-icon><Delete /></el-icon>删除
                    </el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>
          </template>
          <p class="kb-desc">{{ kb.description || '暂无描述' }}</p>
          <div class="kb-stats">
            <span><el-icon><Document /></el-icon>{{ kb.document_count || 0 }} 文档</span>
            <span>{{ kb.chunk_count || 0 }} 分段</span>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-empty v-if="!loading && filteredKBs.length === 0" description="暂无知识库，点击创建" />

    <el-card class="cache-card" shadow="hover" v-loading="cacheLoading">
      <template #header>
        <div class="cache-card-header">
          <span class="card-title">数据库实时监控</span>
          <div class="cache-actions">
            <el-switch
              v-model="cacheAutoRefresh"
              @change="toggleAutoRefresh"
              active-text="自动刷新"
              inactive-text=""
              style="margin-right: 12px"
            />
            <el-button size="small" @click="loadCacheInfo" :icon="Refresh" :loading="cacheLoading">刷新</el-button>
            <el-button size="small" type="danger" @click="handleClearCache('all')" :loading="cacheClearLoading">
              清除全部缓存
            </el-button>
          </div>
        </div>
      </template>
      <el-row :gutter="20">
        <!-- MySQL 状态 -->
        <el-col :xs="24" :md="8">
          <el-descriptions title="MySQL 数据库" :column="1" border v-if="mysqlStatus">
            <el-descriptions-item label="连接状态">
              <el-tag :type="mysqlStatus.connection === 'healthy' ? 'success' : 'danger'" size="small">
                {{ mysqlStatus.connection === 'healthy' ? '已连接' : '未连接' }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="版本">{{ mysqlStatus.version || '-' }}</el-descriptions-item>
            <el-descriptions-item label="数据库名">{{ mysqlStatus.database_name || '-' }}</el-descriptions-item>
            <el-descriptions-item label="表数量">{{ mysqlStatus.table_count ?? '-' }}</el-descriptions-item>
            <el-descriptions-item label="总大小">{{ mysqlStatus.total_size_mb ? mysqlStatus.total_size_mb + ' MB' : '-' }}</el-descriptions-item>
            <el-descriptions-item label="当前连接">{{ mysqlStatus.threads_connected ?? '-' }}</el-descriptions-item>
            <el-descriptions-item label="总查询数">{{ mysqlStatus.questions ?? '-' }}</el-descriptions-item>
            <el-descriptions-item label="慢查询">{{ mysqlStatus.slow_queries ?? '-' }}</el-descriptions-item>
          </el-descriptions>
          <el-empty v-else description="无法获取 MySQL 状态" :image-size="60" />
        </el-col>
        
        <!-- 向量存储状态 -->
        <el-col :xs="24" :md="8">
          <el-descriptions title="向量存储" :column="1" border v-if="vectorStoreStatus">
            <el-descriptions-item label="连接状态">
              <el-tag :type="vectorStoreStatus.connection === 'healthy' ? 'success' : 'danger'" size="small">
                {{ vectorStoreStatus.connection === 'healthy' ? '正常' : '异常' }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="类型">{{ vectorStoreStatus.backend || '-' }}</el-descriptions-item>
            <el-descriptions-item label="索引数量">{{ vectorStoreStatus.index_count ?? '-' }}</el-descriptions-item>
            <el-descriptions-item label="总大小">{{ vectorStoreStatus.total_size_human || '-' }}</el-descriptions-item>
            <el-descriptions-item label="存储路径" :span="1">
              <el-tooltip :content="vectorStoreStatus.base_path" placement="top">
                <span class="truncate-text">{{ vectorStoreStatus.base_path || '-' }}</span>
              </el-tooltip>
            </el-descriptions-item>
          </el-descriptions>
          <el-empty v-else description="无法获取向量存储状态" :image-size="60" />
        </el-col>
        
        <!-- Redis 状态 -->
        <el-col :xs="24" :md="8">
          <el-descriptions title="Redis 缓存" :column="1" border v-if="cacheHealth">
            <el-descriptions-item label="连接状态">
              <el-tag :type="cacheHealth.connection === 'healthy' ? 'success' : 'danger'" size="small">
                {{ cacheHealth.connection === 'healthy' ? '已连接' : '未连接' }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="版本">{{ cacheHealth.version || '-' }}</el-descriptions-item>
            <el-descriptions-item label="内存使用">{{ cacheHealth.used_memory_human || '-' }}</el-descriptions-item>
            <el-descriptions-item label="连接客户端">{{ cacheHealth.connected_clients ?? '-' }}</el-descriptions-item>
            <el-descriptions-item label="总键数">{{ cacheHealth.total_keys ?? '-' }}</el-descriptions-item>
            <el-descriptions-item label="命中率" v-if="cacheStats">
              <el-progress
                :percentage="cacheStats.redis_hit_rate ?? 0"
                :stroke-width="10"
                :color="cacheStats.redis_hit_rate > 80 ? '#67c23a' : cacheStats.redis_hit_rate > 50 ? '#e6a23c' : '#f56c6c'"
                :format="(p) => p + '%'"
              />
            </el-descriptions-item>
          </el-descriptions>
          <el-empty v-else description="无法获取缓存状态" :image-size="60" />
        </el-col>
      </el-row>
      
      <!-- 向量存储索引详情 -->
      <div v-if="vectorStoreStatus?.indices?.length" style="margin-top: 20px;">
        <h4 style="margin-bottom: 12px;">索引详情</h4>
        <el-table :data="vectorStoreStatus.indices" style="width: 100%" size="small">
          <el-table-column prop="original_name" label="索引名称" />
          <el-table-column prop="num_documents" label="文档数" width="100" />
          <el-table-column prop="size_human" label="大小" width="100" />
          <el-table-column prop="created_at" label="创建时间" />
          <el-table-column prop="updated_at" label="更新时间" />
        </el-table>
      </div>
    </el-card>

    <el-dialog v-model="createDialog" title="创建知识库" width="480px">
      <el-form :model="newKB" label-width="80px">
        <el-form-item label="名称">
          <el-input v-model="newKB.name" placeholder="请输入知识库名称" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="newKB.description" type="textarea" :rows="3" placeholder="可选描述" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createDialog = false">取消</el-button>
        <el-button type="primary" @click="handleCreate">创建</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="editDialog" title="编辑知识库" width="480px">
      <el-form :model="currentKBEdit" label-width="80px">
        <el-form-item label="名称">
          <el-input v-model="currentKBEdit.name" disabled />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="currentKBEdit.description" type="textarea" :rows="3" placeholder="可选描述" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editDialog = false">取消</el-button>
        <el-button type="primary" @click="handleEdit">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="detailDialog" title="知识库详情" width="600px">
      <template v-if="currentKB">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="名称">{{ currentKB.name }}</el-descriptions-item>
          <el-descriptions-item label="文档数">{{ currentKB.document_count || 0 }}</el-descriptions-item>
          <el-descriptions-item label="分段数">{{ currentKB.chunk_count || 0 }}</el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ currentKB.created_at }}</el-descriptions-item>
          <el-descriptions-item label="描述" :span="2">{{ currentKB.description || '暂无' }}</el-descriptions-item>
        </el-descriptions>
      </template>
    </el-dialog>

    <el-dialog v-model="uploadDialog" title="上传文档" width="480px">
      <el-upload
        multiple
        drag
        :auto-upload="false"
        :on-change="handleFileChange"
        :file-list="uploadFiles"
        accept=".pdf,.txt,.md,.csv,.json,.docx"
      >
        <el-icon :size="48"><Upload /></el-icon>
        <div>拖拽文件到此处或 <em>点击上传</em></div>
        <template #tip>
          <div class="upload-tip">支持 PDF、TXT、Markdown、CSV、JSON、DOCX 格式，支持多选上传</div>
        </template>
      </el-upload>
      <div v-if="uploadFiles.length > 0" class="upload-file-list">
        <div v-for="(file, idx) in uploadFiles" :key="idx" class="upload-file-item">
          <el-icon><Document /></el-icon>
          <span>{{ file.name }}</span>
          <span class="file-size">{{ formatFileSize(file.size) }}</span>
        </div>
      </div>
      <template #footer>
        <el-button @click="uploadDialog = false" :disabled="uploadLoading">取消</el-button>
        <el-button type="primary" @click="handleUpload" :loading="uploadLoading">
          {{ uploadLoading ? '正在上传并向量化...' : '上传' }}
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="documentsDialog" title="文档管理" width="700px">
      <div class="documents-toolbar">
        <el-button :icon="Plus" @click="openUpload(currentKB)">上传文档</el-button>
        <el-button :icon="Refresh" @click="openDocuments(currentKB)">刷新</el-button>
      </div>
      <div v-loading="documentsLoading">
        <el-table :data="documents" style="width: 100%" v-if="documents.length">
          <el-table-column prop="name" label="文件名" />
          <el-table-column prop="size" label="文件大小">
            <template #default="scope">
              {{ formatFileSize(scope.row.size) }}
            </template>
          </el-table-column>
          <el-table-column prop="uploaded_at" label="上传时间" />
          <el-table-column label="操作" width="120">
            <template #default="scope">
              <el-button type="danger" size="small" @click="handleDeleteDocument(currentKB.id, scope.row.name)">
                删除
              </el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-else description="暂无文档" :image-size="80" />
      </div>
    </el-dialog>

    <el-dialog v-model="testDialog" title="检索测试" width="600px">
      <el-input
        v-model="testQuery"
        placeholder="输入测试查询..."
        @keyup.enter="handleTestSearch"
      >
        <template #append>
          <el-button :icon="Search" :loading="testLoading" @click="handleTestSearch" />
        </template>
      </el-input>
      <div class="test-results" v-if="testResults.length">
        <div v-for="(result, idx) in testResults" :key="idx" class="test-result-item">
          <div class="result-header">
            <el-tag size="small">得分: {{ (result.score * 100).toFixed(1) }}%</el-tag>
            <span class="result-source">{{ result.source || '未知来源' }}</span>
          </div>
          <p class="result-content">{{ result.content }}</p>
        </div>
      </div>
      <el-empty v-else-if="!testLoading" description="输入查询进行检索测试" :image-size="80" />
    </el-dialog>
  </div>
</template>

<style scoped>
.knowledge-container {
  max-width: 1200px;
  margin: 0 auto;
  padding: 32px 24px;
}

.knowledge-header {
  margin-bottom: 24px;
}

.knowledge-header h1 {
  margin: 0 0 8px;
  font-size: 24px;
}

.subtitle {
  color: var(--el-text-color-secondary);
  margin: 0;
}

.knowledge-toolbar {
  display: flex;
  gap: 12px;
  margin-bottom: 24px;
}

.search-input {
  max-width: 300px;
}

.kb-card {
  margin-bottom: 20px;
}

.kb-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.kb-card-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
}

.kb-desc {
  color: var(--el-text-color-secondary);
  font-size: 14px;
  margin: 0 0 12px;
  min-height: 40px;
}

.kb-stats {
  display: flex;
  gap: 16px;
  font-size: 13px;
  color: var(--el-text-color-regular);
}

.kb-stats span {
  display: flex;
  align-items: center;
  gap: 4px;
}

.cache-card {
  margin-top: 24px;
}

.cache-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.cache-card-header .card-title {
  font-weight: 600;
}

.cache-actions {
  display: flex;
  gap: 8px;
}

.documents-toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
}

.test-results {
  margin-top: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.test-result-item {
  padding: 12px;
  background: var(--el-fill-color-lighter);
  border-radius: 8px;
}

.result-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.result-source {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.result-content {
  font-size: 14px;
  margin: 0;
  line-height: 1.6;
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

@media (max-width: 768px) {
  .knowledge-container {
    padding: 16px;
  }

  .knowledge-toolbar {
    flex-direction: column;
  }

  .search-input {
    max-width: 100%;
  }
}

.truncate-text {
  display: inline-block;
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: middle;
}
</style>
