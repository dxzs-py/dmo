<template>
  <div class="task-list">
    <div class="task-list-header">
      <el-input
        v-model="searchQuery"
        placeholder="搜索任务..."
        clearable
        style="width: 300px"
        @input="debounceSearch"
      >
        <template #prefix>
          <el-icon><Search /></el-icon>
        </template>
      </el-input>
      <el-select v-model="statusFilter" placeholder="筛选状态" clearable @change="loadTasks">
        <el-option
          v-for="status in statusOptions"
          :key="status.value"
          :label="status.label"
          :value="status.value"
        />
      </el-select>
      <el-button type="primary" @click="refreshTasks">
        <el-icon><Refresh /></el-icon>
        刷新
      </el-button>
    </div>

    <el-table v-loading="loading" :data="tasks" stripe style="width: 100%">
      <el-table-column prop="id" label="ID" width="100" />
      <el-table-column label="主题/问题" min-width="250">
        <template #default="{ row }">
          <div class="task-query">{{ row.query || row.user_question }}</div>
        </template>
      </el-table-column>
      <el-table-column prop="status" label="状态" width="120">
        <template #default="{ row }">
          <el-tag :type="getStatusType(row.status)">
            {{ getStatusText(row.status) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="创建时间" width="180">
        <template #default="{ row }">
          {{ formatDate(row.created_at) }}
        </template>
      </el-table-column>
      <el-table-column prop="updated_at" label="更新时间" width="180">
        <template #default="{ row }">
          {{ formatDate(row.updated_at) }}
        </template>
      </el-table-column>
      <el-table-column label="操作" width="200" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click="viewTask(row)">
            <el-icon><View /></el-icon>
            查看
          </el-button>
          <el-popconfirm title="确定要删除这个任务吗？" @confirm="deleteTask(row)">
            <template #reference>
              <el-button link type="danger" size="small">
                <el-icon><Delete /></el-icon>
                删除
              </el-button>
            </template>
          </el-popconfirm>
        </template>
      </el-table-column>
    </el-table>

    <div v-if="totalPages > 1" class="pagination">
      <el-pagination
        v-model:current-page="currentPage"
        v-model:page-size="pageSize"
        :page-sizes="[10, 20, 50, 100]"
        :total="total"
        layout="total, sizes, prev, pager, next, jumper"
        @size-change="loadTasks"
        @current-change="loadTasks"
      />
    </div>

    <el-empty v-if="!loading && tasks.length === 0" description="暂无任务" />
  </div>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { Search, Refresh, View, Delete } from '@element-plus/icons-vue'

const props = defineProps({
  moduleType: {
    type: String,
    required: true,
    validator: (value) => ['deep-research', 'workflow'].includes(value),
  },
  api: {
    type: Object,
    required: true,
  },
  statusOptions: {
    type: Array,
    default: () => [],
  },
})

const emit = defineEmits(['view-task', 'delete-task', 'refresh'])

const tasks = ref([])
const loading = ref(false)
const currentPage = ref(1)
const pageSize = ref(20)
const total = ref(0)
const totalPages = computed(() => Math.ceil(total.value / pageSize.value))
const searchQuery = ref('')
const statusFilter = ref('')
let searchTimer = null

const formatDate = (dateStr) => {
  if (!dateStr) return '-'
  const date = new Date(dateStr)
  return date.toLocaleString('zh-CN')
}

const getStatusType = (status) => {
  const typeMap = {
    pending: 'info',
    running: 'warning',
    completed: 'success',
    failed: 'danger',
    waiting_for_answers: 'warning',
    planner: 'primary',
    retrieval: 'primary',
    quiz_generator: 'warning',
    grading: 'primary',
    feedback: 'success',
    end: 'success',
  }
  return typeMap[status] || 'info'
}

const getStatusText = (status) => {
  const textMap = {
    pending: '待执行',
    running: '执行中',
    completed: '已完成',
    failed: '失败',
    waiting_for_answers: '等待答题',
    planner: '生成计划',
    retrieval: '检索资料',
    quiz_generator: '生成题目',
    grading: '评分中',
    feedback: '生成反馈',
    end: '已结束',
  }
  return textMap[status] || status
}

const loadTasks = async () => {
  loading.value = true
  try {
    const params = {
      page: currentPage.value,
      page_size: pageSize.value,
    }
    if (statusFilter.value) {
      params.status = statusFilter.value
    }
    if (searchQuery.value) {
      params.search = searchQuery.value
    }

    const response = await props.api.getTasks(params)
    const data = response.data.data || response.data
    tasks.value = data.items || []
    total.value = data.total || 0
  } catch (error) {
    console.error('加载任务列表失败:', error)
    ElMessage.error('加载任务列表失败')
  } finally {
    loading.value = false
  }
}

const refreshTasks = () => {
  currentPage.value = 1
  loadTasks()
  emit('refresh')
}

const debounceSearch = () => {
  if (searchTimer) {
    clearTimeout(searchTimer)
  }
  searchTimer = setTimeout(() => {
    currentPage.value = 1
    loadTasks()
  }, 300)
}

const viewTask = (task) => {
  emit('view-task', task)
}

const deleteTask = async (task) => {
  try {
    const taskId = task.task_id || task.thread_id
    await props.api.deleteTask(taskId)
    ElMessage.success('删除成功')
    loadTasks()
    emit('delete-task', task)
  } catch (error) {
    console.error('删除任务失败:', error)
    ElMessage.error('删除失败')
  }
}

onMounted(() => {
  loadTasks()
})
</script>

<style scoped>
.task-list {
  padding: 0;
}

.task-list-header {
  display: flex;
  gap: 12px;
  margin-bottom: 16px;
  align-items: center;
}

.task-query {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 300px;
}

.pagination {
  margin-top: 16px;
  display: flex;
  justify-content: flex-end;
}
</style>
