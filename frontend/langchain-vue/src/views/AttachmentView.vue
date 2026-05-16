<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Delete,
  Download,
  FolderAdd,
  RefreshRight,
  Search,
  Refresh,
  Warning,
  CircleCheck,
  Timer,
  Document,
  Histogram,
} from '@element-plus/icons-vue'
import { attachmentAPI } from '@/api/attachment'

const activeTab = ref('files')

const loading = ref(false)
const trashLoading = ref(false)
const statsLoading = ref(false)
const cleanupLoading = ref(false)

const stats = ref({
  storage: {},
  alert: [],
  recent_logs: [],
})

const pagination = reactive({
  page: 1,
  pageSize: 20,
  total: 0,
})

const trashPagination = reactive({
  page: 1,
  pageSize: 20,
  total: 0,
})

const filters = reactive({
  status: '',
  search: '',
  sort_by: '-created_at',
})

const trashSearch = ref('')

const attachments = ref([])
const trashedAttachments = ref([])
const alerts = ref([])

const statusMap = {
  active: { label: '活跃', type: 'success' },
  indexed: { label: '已入库', type: 'warning' },
  pending_delete: { label: '待删除', type: 'danger' },
}

const selectedIds = ref([])
const trashSelectedIds = ref([])

const formatSize = (bytes) => {
  if (!bytes || bytes === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let i = 0
  let size = bytes
  while (size >= 1024 && i < units.length - 1) {
    size /= 1024
    i++
  }
  return `${size.toFixed(i === 0 ? 0 : 2)} ${units[i]}`
}

const formatDate = (dateStr) => {
  if (!dateStr) return '-'
  return new Date(dateStr).toLocaleString('zh-CN')
}

const formatMB = (bytes) => {
  if (!bytes || isNaN(bytes)) return '0 MB'
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

const formatGB = (bytes) => {
  if (!bytes || isNaN(bytes)) return '0 GB'
  return `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GB`
}

const diskUsagePercent = computed(() => {
  return stats.value.storage?.disk_usage_percent || 0
})

const diskUsageStatus = computed(() => {
  const p = diskUsagePercent.value
  if (p >= 95) return 'exception'
  if (p >= 80) return 'warning'
  return 'success'
})

const loadStats = async () => {
  statsLoading.value = true
  try {
    const res = await attachmentAPI.getStats()
    if (res.data?.code === 200) {
      stats.value = res.data.data
    }
  } catch (e) {
    console.error('加载统计失败', e)
  } finally {
    statsLoading.value = false
  }
}

const loadAttachments = async () => {
  loading.value = true
  try {
    const res = await attachmentAPI.getList({
      page: pagination.page,
      page_size: pagination.pageSize,
      status: filters.status || undefined,
      search: filters.search || undefined,
      sort_by: filters.sort_by,
    })
    if (res.data?.code === 200) {
      const data = res.data.data
      attachments.value = data.items || []
      pagination.total = data.total || 0
    }
  } catch (e) {
    console.error('加载附件列表失败', e)
  } finally {
    loading.value = false
  }
}

const loadTrashedAttachments = async () => {
  trashLoading.value = true
  try {
    const res = await attachmentAPI.getTrashedList({
      page: trashPagination.page,
      page_size: trashPagination.pageSize,
      search: trashSearch.value || undefined,
      sort_by: '-created_at',
    })
    if (res.data?.code === 200) {
      const data = res.data.data
      trashedAttachments.value = data.items || []
      trashPagination.total = data.total || 0
    }
  } catch (e) {
    console.error('加载回收站列表失败', e)
  } finally {
    trashLoading.value = false
  }
}

const loadAlerts = async () => {
  try {
    const res = await attachmentAPI.getStorageAlerts()
    if (res.data?.code === 200) {
      alerts.value = res.data.data?.items || res.data.data || []
    }
  } catch (e) {
    console.error('加载告警失败', e)
  }
}

const handlePageChange = (page) => {
  pagination.page = page
  loadAttachments()
}

const handleSizeChange = (size) => {
  pagination.pageSize = size
  pagination.page = 1
  loadAttachments()
}

const handleTrashPageChange = (page) => {
  trashPagination.page = page
  loadTrashedAttachments()
}

const handleTrashSizeChange = (size) => {
  trashPagination.pageSize = size
  trashPagination.page = 1
  loadTrashedAttachments()
}

const handleSearch = () => {
  pagination.page = 1
  loadAttachments()
}

const handleReset = () => {
  filters.status = ''
  filters.search = ''
  filters.sort_by = '-created_at'
  pagination.page = 1
  loadAttachments()
}

const handleTrashSearch = () => {
  trashPagination.page = 1
  loadTrashedAttachments()
}

const handleSelectionChange = (rows) => {
  selectedIds.value = rows.map((r) => r.id)
}

const handleTrashSelectionChange = (rows) => {
  trashSelectedIds.value = rows.map((r) => r.id)
}

const handleDelete = async (row) => {
  try {
    await ElMessageBox.confirm(
      `确定要删除文件 "${row.original_name}" 吗？文件将移入回收站，可从回收站恢复。`,
      '确认删除',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    )
    const res = await attachmentAPI.deleteAttachment(row.id)
    if (res.data?.code === 200) {
      ElMessage.success('已移入回收站')
      loadAttachments()
      loadStats()
    } else {
      ElMessage.error(res.data?.message || '删除失败')
    }
  } catch {
    // cancelled
  }
}

const handleIndex = async (row) => {
  try {
    const res = await attachmentAPI.indexAttachment(row.id)
    if (res.data?.code === 200) {
      ElMessage.success('向量化入库成功')
      loadAttachments()
      loadStats()
    } else {
      ElMessage.error(res.data?.message || '入库失败')
    }
  } catch (e) {
    ElMessage.error('入库失败')
  }
}

const handleUnindex = async (row) => {
  try {
    const res = await attachmentAPI.unindexAttachment(row.id)
    if (res.data?.code === 200) {
      ElMessage.success('已从向量库移除')
      loadAttachments()
      loadStats()
    } else {
      ElMessage.error(res.data?.message || '移除失败')
    }
  } catch (e) {
    ElMessage.error('移除失败')
  }
}

const handleRestore = async (row) => {
  try {
    const res = await attachmentAPI.actionAttachment(row.id, 'restore')
    if (res.data?.code === 200) {
      ElMessage.success('恢复成功')
      loadAttachments()
      loadStats()
    } else {
      ElMessage.error(res.data?.message || '恢复失败')
    }
  } catch (e) {
    ElMessage.error('恢复失败')
  }
}

const handleUpdateRetention = async (row) => {
  try {
    const { value } = await ElMessageBox.prompt('请输入保留天数', '设置保留天数', {
      inputValue: row.retention_days || 30,
      inputPattern: /^\d+$/,
      inputErrorMessage: '请输入有效数字',
    })
    const res = await attachmentAPI.actionAttachment(row.id, 'update_retention', {
      retention_days: parseInt(value),
    })
    if (res.data?.code === 200) {
      ElMessage.success('更新成功')
      loadAttachments()
    } else {
      ElMessage.error(res.data?.message || '更新失败')
    }
  } catch {
    // cancelled
  }
}

const handlePermanentDelete = async (row) => {
  try {
    await ElMessageBox.confirm(
      `确定要永久删除文件 "${row.original_name}" 吗？此操作不可恢复！`,
      '永久删除',
      { type: 'error', confirmButtonText: '永久删除', cancelButtonText: '取消', confirmButtonClass: 'el-button--danger' }
    )
    const res = await attachmentAPI.permanentDelete(row.id)
    if (res.data?.code === 200) {
      ElMessage.success('已永久删除')
      loadTrashedAttachments()
      loadStats()
    } else {
      ElMessage.error(res.data?.message || '永久删除失败')
    }
  } catch {
    // cancelled
  }
}

const handleRestoreFromTrash = async (row) => {
  try {
    const res = await attachmentAPI.restoreFromTrash(row.id)
    if (res.data?.code === 200) {
      ElMessage.success('已恢复到文件列表')
      loadTrashedAttachments()
      loadStats()
    } else {
      ElMessage.error(res.data?.message || '恢复失败')
    }
  } catch (e) {
    ElMessage.error('恢复失败')
  }
}

const handleBatchDelete = async () => {
  if (selectedIds.value.length === 0) {
    ElMessage.warning('请先选择文件')
    return
  }
  try {
    await ElMessageBox.confirm(
      `确定要删除选中的 ${selectedIds.value.length} 个文件吗？文件将移入回收站。`,
      '批量删除',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    )
    const res = await attachmentAPI.batchDelete(selectedIds.value)
    if (res.data?.code === 200) {
      const data = res.data.data
      ElMessage.success(`批量删除完成: 成功 ${data.success.length} 个, 失败 ${data.failed.length} 个`)
      selectedIds.value = []
      loadAttachments()
      loadStats()
    } else {
      ElMessage.error(res.data?.message || '批量删除失败')
    }
  } catch {
    // cancelled
  }
}

const handleBatchIndex = async () => {
  if (selectedIds.value.length === 0) {
    ElMessage.warning('请先选择文件')
    return
  }
  try {
    const res = await attachmentAPI.batchIndex(selectedIds.value)
    if (res.data?.code === 200) {
      const data = res.data.data
      ElMessage.success(`批量入库完成: 成功 ${data.success.length} 个, 失败 ${data.failed.length} 个`)
      selectedIds.value = []
      loadAttachments()
      loadStats()
    } else {
      ElMessage.error(res.data?.message || '批量入库失败')
    }
  } catch (e) {
    ElMessage.error('批量入库失败')
  }
}

const handleBatchPermanentDelete = async () => {
  if (trashSelectedIds.value.length === 0) {
    ElMessage.warning('请先选择文件')
    return
  }
  try {
    await ElMessageBox.confirm(
      `确定要永久删除选中的 ${trashSelectedIds.value.length} 个文件吗？此操作不可恢复！`,
      '批量永久删除',
      { type: 'error', confirmButtonClass: 'el-button--danger' }
    )
    let successCount = 0
    let failCount = 0
    for (const id of trashSelectedIds.value) {
      try {
        await attachmentAPI.permanentDelete(id)
        successCount++
      } catch {
        failCount++
      }
    }
    if (failCount === 0) {
      ElMessage.success(`已永久删除 ${successCount} 个文件`)
    } else {
      ElMessage.warning(`删除完成：成功 ${successCount} 个，失败 ${failCount} 个`)
    }
    trashSelectedIds.value = []
    loadTrashedAttachments()
    loadStats()
  } catch {
    // cancelled
  }
}

const handleBatchRestoreFromTrash = async () => {
  if (trashSelectedIds.value.length === 0) {
    ElMessage.warning('请先选择文件')
    return
  }
  try {
    let successCount = 0
    let failCount = 0
    for (const id of trashSelectedIds.value) {
      try {
        await attachmentAPI.restoreFromTrash(id)
        successCount++
      } catch {
        failCount++
      }
    }
    if (failCount === 0) {
      ElMessage.success(`已恢复 ${successCount} 个文件`)
    } else {
      ElMessage.warning(`恢复完成：成功 ${successCount} 个，失败 ${failCount} 个`)
    }
    trashSelectedIds.value = []
    loadTrashedAttachments()
    loadStats()
  } catch (e) {
    ElMessage.error('批量恢复失败')
  }
}

const handleCleanup = async (action, dryRun = false) => {
  const actionLabel = action === 'cleanup' ? '清理' : '入库'
  const label = dryRun ? `模拟${actionLabel}` : actionLabel

  if (!dryRun) {
    try {
      await ElMessageBox.confirm(
        `确定要执行${label}操作吗？`,
        `确认${label}`,
        { type: 'warning' }
      )
    } catch {
      return
    }
  }

  cleanupLoading.value = true
  try {
    const res = await attachmentAPI.runCleanup(action, dryRun)
    if (res.data?.code === 200) {
      const data = res.data.data
      ElMessage.success(
        `${label}完成: 处理=${data.files_processed}, ` +
          `${action === 'cleanup' ? '删除' : '入库'}=${action === 'cleanup' ? data.files_deleted : data.files_archived}`
      )
      loadAttachments()
      loadStats()
    } else {
      ElMessage.error(res.data?.message || `${label}失败`)
    }
  } catch (e) {
    ElMessage.error(`${label}失败`)
  } finally {
    cleanupLoading.value = false
  }
}

const handleAlertAction = async (alertId, action) => {
  try {
    const res = await attachmentAPI.handleStorageAlert(alertId, action)
    if (res.data?.code === 200) {
      ElMessage.success('操作成功')
      loadAlerts()
      loadStats()
    } else {
      ElMessage.error(res.data?.message || '操作失败')
    }
  } catch (e) {
    ElMessage.error('操作失败')
  }
}

const handleTabChange = (tab) => {
  if (tab === 'trash') {
    loadTrashedAttachments()
  }
}

onMounted(() => {
  loadStats()
  loadAttachments()
  loadAlerts()
})
</script>

<template>
  <div class="attachment-view">
    <div class="view-content">
      <!-- 存储概览 -->
      <el-card class="stats-card" shadow="never">
        <template #header>
          <div class="card-header">
            <span class="card-title">
              <el-icon><Histogram /></el-icon>
              存储概览
            </span>
            <el-button :icon="Refresh" text size="small" @click="loadStats" :loading="statsLoading">
              刷新
            </el-button>
          </div>
        </template>

        <div class="stats-grid">
          <div class="stat-item">
            <div class="stat-value">{{ stats.storage?.total_files || 0 }}</div>
            <div class="stat-label">总文件数</div>
          </div>
          <div class="stat-item">
            <div class="stat-value">{{ stats.storage?.active_files || 0 }}</div>
            <div class="stat-label">活跃文件</div>
          </div>
          <div class="stat-item">
            <div class="stat-value">{{ stats.storage?.indexed_files || 0 }}</div>
            <div class="stat-label">已入库</div>
          </div>
          <div class="stat-item">
            <div class="stat-value">{{ stats.storage?.trashed_files || 0 }}</div>
            <div class="stat-label">回收站</div>
          </div>
          <div class="stat-item">
            <div class="stat-value">{{ formatSize(stats.storage?.total_size_bytes) }}</div>
            <div class="stat-label">附件总大小</div>
          </div>
          <div class="stat-item disk-usage">
            <div class="stat-value">{{ diskUsagePercent.toFixed(1) }}%</div>
            <div class="stat-label">磁盘使用率</div>
            <el-progress
              :percentage="diskUsagePercent"
              :status="diskUsageStatus"
              :stroke-width="8"
              style="margin-top: 8px"
            />
          </div>
          <div class="stat-item">
            <div class="stat-value">{{ formatGB(stats.storage?.disk_free_bytes) }}</div>
            <div class="stat-label">磁盘剩余</div>
          </div>
        </div>

        <div v-if="stats.alert && stats.alert.length > 0" class="alert-section">
          <el-alert
            v-for="alert in stats.alert"
            :key="alert.id"
            :title="alert.message"
            :type="alert.level === 'critical' ? 'error' : 'warning'"
            show-icon
            :closable="false"
            style="margin-bottom: 8px"
          />
        </div>
      </el-card>

      <!-- 生命周期操作 -->
      <el-card shadow="never" style="margin-top: 16px">
        <template #header>
          <div class="card-header">
            <span class="card-title">
              <el-icon><Timer /></el-icon>
              生命周期操作
            </span>
          </div>
        </template>
        <div class="action-bar">
          <el-button
            :icon="Delete"
            type="danger"
            plain
            @click="handleCleanup('cleanup', true)"
            :loading="cleanupLoading"
          >
            模拟清理
          </el-button>
          <el-button
            :icon="Delete"
            type="danger"
            @click="handleCleanup('cleanup', false)"
            :loading="cleanupLoading"
          >
            执行清理
          </el-button>
          <el-button
            :icon="FolderAdd"
            type="warning"
            plain
            @click="handleCleanup('index', true)"
            :loading="cleanupLoading"
          >
            模拟入库
          </el-button>
          <el-button
            :icon="FolderAdd"
            type="warning"
            @click="handleCleanup('index', false)"
            :loading="cleanupLoading"
          >
            执行入库
          </el-button>
        </div>
      </el-card>

      <!-- 文件管理 + 回收站 Tab -->
      <el-card shadow="never" style="margin-top: 16px">
        <el-tabs v-model="activeTab" @tab-change="handleTabChange">
          <!-- 文件管理 Tab -->
          <el-tab-pane label="文件管理" name="files">
            <div class="tab-header">
              <div class="filter-bar">
                <el-input
                  v-model="filters.search"
                  placeholder="搜索文件名"
                  :prefix-icon="Search"
                  clearable
                  size="small"
                  style="width: 200px"
                  @keyup.enter="handleSearch"
                  @clear="handleSearch"
                />
                <el-select v-model="filters.status" placeholder="状态" clearable size="small" style="width: 120px" @change="handleSearch">
                  <el-option label="活跃" value="active" />
                  <el-option label="已入库" value="indexed" />
                </el-select>
                <el-button :icon="Refresh" text size="small" @click="handleReset">重置</el-button>
              </div>
            </div>

            <div v-if="selectedIds.length > 0" class="batch-bar">
              <span>已选择 {{ selectedIds.length }} 项</span>
              <el-button type="danger" size="small" @click="handleBatchDelete">批量删除</el-button>
              <el-button type="warning" size="small" @click="handleBatchIndex">批量入库</el-button>
            </div>

            <el-table
              :data="attachments"
              v-loading="loading"
              @selection-change="handleSelectionChange"
              stripe
              style="width: 100%"
            >
              <el-table-column type="selection" width="40" />
              <el-table-column prop="original_name" label="文件名" min-width="180" show-overflow-tooltip />
              <el-table-column prop="file_type" label="类型" width="80" />
              <el-table-column label="大小" width="100">
                <template #default="{ row }">{{ formatSize(row.file_size) }}</template>
              </el-table-column>
              <el-table-column label="状态" width="100">
                <template #default="{ row }">
                  <el-tag :type="statusMap[row.status]?.type || 'info'" size="small">
                    {{ statusMap[row.status]?.label || row.status }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="reference_count" label="引用" width="70" align="center" />
              <el-table-column label="保留天数" width="90" align="center">
                <template #default="{ row }">{{ row.retention_days || '-' }}</template>
              </el-table-column>
              <el-table-column label="上传时间" width="160">
                <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
              </el-table-column>
              <el-table-column label="操作" width="220" fixed="right">
                <template #default="{ row }">
                  <el-button
                    v-if="row.status === 'active'"
                    type="warning"
                    text
                    size="small"
                    :icon="FolderAdd"
                    @click="handleIndex(row)"
                  >
                    入库
                  </el-button>
                  <el-button
                    v-if="row.status === 'indexed'"
                    type="success"
                    text
                    size="small"
                    :icon="RefreshRight"
                    @click="handleUnindex(row)"
                  >
                    移除
                  </el-button>
                  <el-button
                    type="primary"
                    text
                    size="small"
                    :icon="Timer"
                    @click="handleUpdateRetention(row)"
                  >
                    保留期
                  </el-button>
                  <el-button
                    type="danger"
                    text
                    size="small"
                    :icon="Delete"
                    @click="handleDelete(row)"
                  >
                    删除
                  </el-button>
                </template>
              </el-table-column>
            </el-table>

            <div class="pagination-wrapper">
              <el-pagination
                v-model:current-page="pagination.page"
                v-model:page-size="pagination.pageSize"
                :page-sizes="[10, 20, 50, 100]"
                :total="pagination.total"
                layout="total, sizes, prev, pager, next"
                @current-change="handlePageChange"
                @size-change="handleSizeChange"
              />
            </div>
          </el-tab-pane>

          <!-- 回收站 Tab -->
          <el-tab-pane name="trash">
            <template #label>
              <span>
                回收站
                <el-badge v-if="stats.storage?.trashed_files > 0" :value="stats.storage.trashed_files" class="tab-badge" />
              </span>
            </template>

            <div class="tab-header">
              <div class="filter-bar">
                <el-input
                  v-model="trashSearch"
                  placeholder="搜索文件名"
                  :prefix-icon="Search"
                  clearable
                  size="small"
                  style="width: 200px"
                  @keyup.enter="handleTrashSearch"
                  @clear="handleTrashSearch"
                />
              </div>
            </div>

            <div v-if="trashSelectedIds.length > 0" class="batch-bar">
              <span>已选择 {{ trashSelectedIds.length }} 项</span>
              <el-button type="success" size="small" @click="handleBatchRestoreFromTrash">批量恢复</el-button>
              <el-button type="danger" size="small" @click="handleBatchPermanentDelete">批量永久删除</el-button>
            </div>

            <el-table
              :data="trashedAttachments"
              v-loading="trashLoading"
              @selection-change="handleTrashSelectionChange"
              stripe
              style="width: 100%"
            >
              <el-table-column type="selection" width="40" />
              <el-table-column prop="original_name" label="文件名" min-width="180" show-overflow-tooltip />
              <el-table-column prop="file_type" label="类型" width="80" />
              <el-table-column label="大小" width="100">
                <template #default="{ row }">{{ formatSize(row.file_size) }}</template>
              </el-table-column>
              <el-table-column label="删除时间" width="160">
                <template #default="{ row }">{{ formatDate(row.deleted_at) }}</template>
              </el-table-column>
              <el-table-column label="所属会话" min-width="140" show-overflow-tooltip>
                <template #default="{ row }">{{ row.session_title || row.session_id || '-' }}</template>
              </el-table-column>
              <el-table-column label="操作" width="180" fixed="right">
                <template #default="{ row }">
                  <el-button
                    type="success"
                    text
                    size="small"
                    :icon="RefreshRight"
                    @click="handleRestoreFromTrash(row)"
                  >
                    恢复
                  </el-button>
                  <el-button
                    type="danger"
                    text
                    size="small"
                    :icon="Delete"
                    @click="handlePermanentDelete(row)"
                  >
                    永久删除
                  </el-button>
                </template>
              </el-table-column>
            </el-table>

            <div class="pagination-wrapper">
              <el-pagination
                v-model:current-page="trashPagination.page"
                v-model:page-size="trashPagination.pageSize"
                :page-sizes="[10, 20, 50, 100]"
                :total="trashPagination.total"
                layout="total, sizes, prev, pager, next"
                @current-change="handleTrashPageChange"
                @size-change="handleTrashSizeChange"
              />
            </div>
          </el-tab-pane>
        </el-tabs>
      </el-card>

      <!-- 告警记录 -->
      <el-card shadow="never" style="margin-top: 16px">
        <template #header>
          <div class="card-header">
            <span class="card-title">
              <el-icon><Warning /></el-icon>
              存储告警
            </span>
            <el-button :icon="Refresh" text size="small" @click="loadAlerts">刷新</el-button>
          </div>
        </template>

        <el-table :data="alerts" stripe style="width: 100%" empty-text="暂无告警">
          <el-table-column label="级别" width="80">
            <template #default="{ row }">
              <el-tag :type="row.level === 'critical' ? 'danger' : 'warning'" size="small">
                {{ row.level === 'critical' ? '严重' : '警告' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="message" label="告警信息" min-width="250" show-overflow-tooltip />
          <el-table-column label="使用率" width="100">
            <template #default="{ row }">{{ row.usage_percent?.toFixed(1) }}%</template>
          </el-table-column>
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <el-tag
                :type="row.status === 'active' ? 'danger' : row.status === 'acknowledged' ? 'warning' : 'success'"
                size="small"
              >
                {{ row.status === 'active' ? '活跃' : row.status === 'acknowledged' ? '已确认' : '已解决' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="时间" width="160">
            <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="160">
            <template #default="{ row }">
              <el-button
                v-if="row.status === 'active'"
                type="warning"
                text
                size="small"
                @click="handleAlertAction(row.id, 'acknowledge')"
              >
                确认
              </el-button>
              <el-button
                v-if="row.status !== 'resolved'"
                type="success"
                text
                size="small"
                @click="handleAlertAction(row.id, 'resolve')"
              >
                解决
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-card>

      <!-- 清理日志 -->
      <el-card v-if="stats.recent_logs && stats.recent_logs.length > 0" shadow="never" style="margin-top: 16px">
        <template #header>
          <div class="card-header">
            <span class="card-title">
              <el-icon><CircleCheck /></el-icon>
              最近清理日志
            </span>
          </div>
        </template>
        <el-table :data="stats.recent_logs" stripe style="width: 100%">
          <el-table-column prop="action" label="操作" width="100" />
          <el-table-column prop="files_processed" label="处理数" width="80" />
          <el-table-column prop="files_deleted" label="删除数" width="80" />
          <el-table-column prop="files_archived" label="入库数" width="80" />
          <el-table-column label="释放空间" width="100">
            <template #default="{ row }">{{ formatMB(row.space_freed) }}</template>
          </el-table-column>
          <el-table-column prop="triggered_by" label="触发方式" width="100" />
          <el-table-column label="执行时间" min-width="160">
            <template #default="{ row }">{{ formatDate(row.started_at) }}</template>
          </el-table-column>
        </el-table>
      </el-card>
    </div>
  </div>
</template>

<style scoped>
.attachment-view {
  height: 100%;
  overflow-y: auto;
  padding: 20px;
}

.view-content {
  max-width: 1200px;
  margin: 0 auto;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.card-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 16px;
  font-weight: 600;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 16px;
}

.stat-item {
  text-align: center;
  padding: 12px;
  border-radius: 8px;
  background: var(--el-fill-color-lighter);
}

.stat-value {
  font-size: 24px;
  font-weight: 700;
  color: var(--el-text-color-primary);
}

.stat-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 4px;
}

.disk-usage .stat-value {
  color: var(--el-color-primary);
}

.alert-section {
  margin-top: 16px;
}

.tab-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.filter-bar {
  display: flex;
  align-items: center;
  gap: 8px;
}

.action-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.batch-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 12px;
  margin-bottom: 12px;
  background: var(--el-color-primary-light-9);
  border-radius: 6px;
  font-size: 13px;
  color: var(--el-color-primary);
}

.pagination-wrapper {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}

.tab-badge {
  margin-left: 4px;
}

.tab-badge :deep(.el-badge__content) {
  font-size: 10px;
}

@media (max-width: 1024px) {
  .attachment-view {
    padding: 16px;
  }
}

@media (max-width: 768px) {
  .attachment-view {
    padding: 12px;
  }

  .card-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }

  .filter-bar {
    flex-wrap: wrap;
  }

  .stats-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}

@media (max-width: 480px) {
  .attachment-view {
    padding: 8px;
  }

  .stats-grid {
    grid-template-columns: 1fr 1fr;
    gap: 8px;
  }

  .stat-value {
    font-size: 20px;
  }

  .action-bar {
    flex-direction: column;
  }

  .batch-bar {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }
}
</style>
