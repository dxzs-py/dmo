<template>
  <div class="file-browser">
    <div class="file-browser-header">
      <span class="file-count">
        <el-icon><Document /></el-icon>
        共 {{ files.length }} 个文件
      </span>
      <el-button type="primary" size="small" @click="refreshFiles">
        <el-icon><Refresh /></el-icon>
        刷新
      </el-button>
    </div>

    <div v-loading="loading" class="file-list">
      <el-empty v-if="!loading && files.length === 0" description="暂无文件" />

      <div v-else class="file-grid">
        <div
          v-for="file in files"
          :key="file.relative_path"
          class="file-item"
          @click="viewFile(file)"
        >
          <div class="file-icon">
            <el-icon :size="32">
              <Document v-if="file.extension === '.md'" />
              <DocumentCopy v-else-if="file.extension === '.json'" />
              <Tickets v-else-if="file.extension === '.txt'" />
              <Folder v-else />
            </el-icon>
          </div>
          <div class="file-info">
            <div class="file-name" :title="file.name">{{ file.name }}</div>
            <div class="file-meta">
              <span class="file-type">{{ file.file_type }}</span>
              <span class="file-size">{{ file.size_formatted }}</span>
            </div>
          </div>
          <div class="file-actions" @click.stop>
            <el-button link type="primary" size="small" @click="downloadFile(file)">
              <el-icon><Download /></el-icon>
            </el-button>
            <el-button link type="primary" size="small" @click="viewFile(file)">
              <el-icon><View /></el-icon>
            </el-button>
          </div>
        </div>
      </div>
    </div>

    <el-dialog
      v-model="previewVisible"
      :title="previewFile?.name"
      width="70%"
      top="5vh"
    >
      <div class="file-preview">
        <div v-if="previewLoading" class="preview-loading">
          <el-icon class="is-loading"><Loading /></el-icon>
          加载中...
        </div>
        <div v-else-if="previewContent" class="preview-content">
          <div v-if="isTextFile" class="markdown-preview">
            <MarkdownRenderer :content="previewContent" />
          </div>
          <pre v-else>{{ previewContent }}</pre>
        </div>
        <el-empty v-else description="无法预览此文件" />
      </div>
      <template #footer>
        <el-button @click="previewVisible = false">关闭</el-button>
        <el-button type="primary" @click="downloadFile(previewFile)">
          <el-icon><Download /></el-icon>
          下载
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import {
  Document,
  DocumentCopy,
  Tickets,
  Folder,
  Refresh,
  Download,
  View,
  Loading,
} from '@element-plus/icons-vue'
import MarkdownRenderer from './MarkdownRenderer.vue'
import { useUserStore } from '@/stores/user'

const props = defineProps({
  taskId: {
    type: String,
    required: true,
  },
  api: {
    type: Object,
    required: true,
  },
})

const emit = defineEmits(['refresh'])

const files = ref([])
const loading = ref(false)
const previewVisible = ref(false)
const previewFile = ref(null)
const previewContent = ref('')
const previewLoading = ref(false)
const userStore = useUserStore()

const isTextFile = computed(() => {
  if (!previewFile.value) return false
  const ext = previewFile.value.extension?.toLowerCase()
  return ['.md', '.txt', '.json'].includes(ext)
})

const loadFiles = async () => {
  loading.value = true
  try {
    const response = await props.api.getFiles(props.taskId)
    const data = response.data.data || response.data
    files.value = data.files || []
  } catch (error) {
    console.error('加载文件列表失败:', error)
    ElMessage.error('加载文件列表失败')
  } finally {
    loading.value = false
  }
}

const refreshFiles = () => {
  loadFiles()
  emit('refresh')
}

const downloadFile = (file) => {
  try {
    const url = props.api.downloadFile(props.taskId, file.name)
    const link = document.createElement('a')
    link.href = url
    link.download = file.name
    link.target = '_blank'
    if (userStore.token) {
      link.href += (url.includes('?') ? '&' : '?') + `token=${userStore.token}`
    }
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    ElMessage.success('开始下载')
  } catch (error) {
    console.error('下载文件失败:', error)
    ElMessage.error('下载文件失败')
  }
}

const viewFile = async (file) => {
  previewFile.value = file
  previewVisible.value = true
  previewContent.value = ''
  previewLoading.value = true

  try {
    const response = await props.api.getFileContent(props.taskId, file.name)
    const data = response.data.data || response.data
    previewContent.value = data.content || ''
  } catch (error) {
    console.error('加载文件内容失败:', error)
    ElMessage.error('加载文件内容失败')
  } finally {
    previewLoading.value = false
  }
}

defineExpose({
  loadFiles,
  refreshFiles,
})
</script>

<style scoped>
.file-browser {
  padding: 0;
}

.file-browser-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.file-count {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--el-text-color-regular);
}

.file-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
}

.file-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-bg-color);
  cursor: pointer;
  transition: all 0.2s;
}

.file-item:hover {
  border-color: var(--el-color-primary);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
}

.file-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  border-radius: 8px;
  background: var(--el-fill-color-lighter);
  color: var(--el-color-primary);
}

.file-info {
  flex: 1;
  min-width: 0;
}

.file-name {
  font-weight: 500;
  font-size: 14px;
  color: var(--el-text-color-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-meta {
  display: flex;
  gap: 12px;
  margin-top: 4px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.file-type {
  padding: 0 6px;
  background: var(--el-fill-color-lighter);
  border-radius: 4px;
}

.file-actions {
  display: flex;
  gap: 4px;
}

.file-preview {
  max-height: 60vh;
  overflow-y: auto;
}

.preview-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px;
  gap: 8px;
  color: var(--el-text-color-secondary);
}

.preview-content {
  padding: 0;
}

.markdown-preview {
  padding: 16px;
  background: var(--el-fill-color-lighter);
  border-radius: 4px;
}

.preview-content pre {
  margin: 0;
  padding: 16px;
  background: var(--el-fill-color-lighter);
  border-radius: 4px;
  overflow-x: auto;
  font-size: 13px;
  line-height: 1.6;
}
</style>
