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
          :key="file.relativePath"
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
              <span class="file-type">{{ file.fileType }}</span>
              <span class="file-size">{{ file.sizeFormatted }}</span>
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
import { ref, computed, watch } from 'vue'
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
import MarkdownRenderer from '../common/MarkdownRenderer.vue'
import { useUserStore } from '@/stores/user'
import { useAutoRefresh } from '@/composables/useAutoRefresh'
import { logger } from '../../utils/logger'

const props = defineProps({
  taskId: {
    type: String,
    required: true,
  },
  api: {
    type: Object,
    required: true,
  },
  /** 是否自动加载并在文件未就绪时轮询（默认开启） */
  autoRefresh: {
    type: Boolean,
    default: true,
  },
  /** 轮询间隔（ms） */
  refreshInterval: {
    type: Number,
    default: 3000,
  },
  /** 最大轮询次数（0 表示无限，有上限保护需求时设置） */
  maxRefreshAttempts: {
    type: Number,
    default: 60,
  },
})

const emit = defineEmits(['refresh'])

const files = ref([])
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

// ── 自动加载：文件列表就绪（非空且连续 3 次无变化）后停止轮询 ──
let lastSignature = null
let stableCount = 0

const getFileSignature = (list) =>
  list.map(f => `${f.relativePath || f.name}:${f.size ?? ''}`).join('|')

const shouldStopAutoRefresh = (data) => {
  const list = data.files || []
  if (list.length === 0) {
    lastSignature = null
    stableCount = 0
    return false
  }
  const signature = getFileSignature(list)
  if (signature === lastSignature) {
    stableCount += 1
  } else {
    lastSignature = signature
    stableCount = 1
  }
  return stableCount >= 3
}

const fetcher = () =>
  props.api.getFiles(props.taskId).then(response => response.data.data || response.data)

const { loading, refresh, start, stop } = useAutoRefresh(fetcher, {
  interval: props.refreshInterval,
  maxAttempts: props.maxRefreshAttempts,
  shouldStop: shouldStopAutoRefresh,
  onData: (data) => {
    files.value = data.files || []
  },
  source: 'FileBrowser',
})

// taskId 切换（切换任务/执行）时重启自动加载
watch(
  () => props.taskId,
  () => {
    lastSignature = null
    stableCount = 0
    files.value = []
    stop()
    if (props.autoRefresh) start()
  },
)

// autoRefresh 开关变化时启停
watch(
  () => props.autoRefresh,
  (enabled) => {
    if (enabled) start()
    else stop()
  },
)

/** 手动刷新（不参与自动轮询计数） */
const refreshFiles = async () => {
  await refresh()
  emit('refresh')
}

/** 供外部（任务终态等场景）主动触发一次刷新 */
const loadFiles = async () => {
  await refresh()
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
    logger.error('下载文件失败:', error)
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
    logger.error('加载文件内容失败:', error)
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
