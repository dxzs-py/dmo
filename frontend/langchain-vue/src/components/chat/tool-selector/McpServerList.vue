<script setup>
import { ref, computed, watch, inject } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Edit, Check, Warning, VideoPlay, Refresh } from '@element-plus/icons-vue'
import { toolsAPI } from '@/api/tools'
import { useToolsStore } from '@/stores/tools'
import McpUploadDialog from '../McpUploadDialog.vue'

const props = defineProps({
  selected: {
    type: Array,
    default: () => [],
  },
})

const emit = defineEmits({
  'update:selected': (val) => Array.isArray(val),
  'dialog-visibility-change': (val) => typeof val === 'boolean',
})

const toolsStore = useToolsStore()
/** 父容器（popover 拥有者）注入的 popover 锁包裹函数：异步操作期间锁定 popover 不关闭 */
const withPopoverLock = inject('toolSelectorWithPopoverLock')

// ── 数据（从 store 读取） ──
const mcpServers = computed(() => toolsStore.mcpServers)
const mcpLoading = computed(() => toolsStore.mcpLoading)
const systemMcpServers = computed(() => toolsStore.systemMcpServers)
const userMcpServers = computed(() => toolsStore.userMcpServers)

const fetchMcpServers = (force = false) => toolsStore.loadMcpServers(force)

// ── 对话框 ──
const showMcpUploadDialog = ref(false)
const editingMcpServer = ref(null)

// 上报对话框可见性，父容器据此锁定 popover
watch(showMcpUploadDialog, (open) => {
  emit('dialog-visibility-change', open)
})

/** 供父容器 header「上传/添加」按钮调用：以新增模式打开上传对话框 */
const openUploadDialog = () => {
  editingMcpServer.value = null
  showMcpUploadDialog.value = true
}
defineExpose({ openUploadDialog })

// ── 选择操作 ──
const isMcpSelected = (name) => props.selected.includes(name)

const toggleMcpServer = (name) => {
  const idx = props.selected.indexOf(name)
  if (idx >= 0) {
    emit('update:selected', props.selected.filter(n => n !== name))
  } else {
    emit('update:selected', [...props.selected, name])
  }
}

const selectAllMcp = () => {
  emit('update:selected', mcpServers.value.map(s => s.name))
}

const clearMcp = () => {
  emit('update:selected', [])
}

const handleDeleteMcpServer = (name) => withPopoverLock(async () => {
  try {
    await ElMessageBox.confirm(
      `确定删除 MCP Server "${name}" 吗？`,
      '删除确认',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' },
    )
  } catch { return }
  try {
    await toolsAPI.deleteMcpServer(name)
    ElMessage.success(`MCP Server "${name}" 已删除`)
    emit('update:selected', props.selected.filter(n => n !== name))
    toolsStore.invalidateMcpServers()
    fetchMcpServers()
  } catch (e) {
    ElMessage.error('删除失败: ' + (e.response?.data?.message || e.message))
  }
})

// ── 测试连接 ──
const mcpTestingName = ref(null)
const mcpTestResult = ref(null)

const handleTestMcpServer = (name) => withPopoverLock(async () => {
  mcpTestingName.value = name
  mcpTestResult.value = null
  try {
    const res = await toolsAPI.testMcpServer(name)
    const data = res.data?.data || res.data
    if (data.connected) {
      mcpTestResult.value = { name, success: true, message: `连接成功，共 ${data.toolCount} 个工具` }
    } else {
      mcpTestResult.value = { name, success: false, message: `连接失败${data.error ? '：' + data.error : ''}` }
    }
  } catch (e) {
    mcpTestResult.value = { name, success: false, message: '连接测试失败: ' + (e.response?.data?.message || e.message) }
  } finally {
    mcpTestingName.value = null
  }
})

const handleEditMcpServer = (srv) => {
  editingMcpServer.value = { ...srv }
  showMcpUploadDialog.value = true
}

const handleMcpAddSuccess = async () => {
  showMcpUploadDialog.value = false
  editingMcpServer.value = null
  toolsStore.invalidateMcpServers()
  await fetchMcpServers(true)
}
</script>

<template>
  <div class="tab-batch-actions">
    <el-button size="small" link type="primary" @click="selectAllMcp">全选</el-button>
    <el-button size="small" link type="info" @click="clearMcp">清空</el-button>
  </div>

  <div v-if="mcpLoading" class="tool-loading">
    <el-icon class="is-loading"><Refresh /></el-icon> 加载中...
  </div>
  <div v-else-if="mcpServers.length === 0" class="tool-empty">暂无 MCP 服务器</div>
  <div v-else class="tool-list">
    <div v-if="systemMcpServers.length > 0" class="tool-group">
      <div class="tool-group-label">系统内置</div>
      <div
        v-for="srv in systemMcpServers"
        :key="srv.name"
        :class="['tool-item', { selected: isMcpSelected(srv.name) }]"
      >
        <div class="tool-info">
          <span class="tool-name">
            {{ srv.name }}
            <el-tag size="small" type="info" effect="plain">{{ srv.transport }}</el-tag>
          </span>
          <span v-if="srv.description" class="tool-desc">{{ srv.description }}</span>
          <div v-if="mcpTestResult && mcpTestResult.name === srv.name" class="test-result-inline" :class="mcpTestResult.success ? 'success' : 'error'">
            <el-icon v-if="mcpTestResult.success"><Check /></el-icon>
            <el-icon v-else><Warning /></el-icon>
            <span>{{ mcpTestResult.message }}</span>
          </div>
        </div>
        <div class="tool-actions">
          <el-button
            :icon="VideoPlay"
            size="small"
            circle
            :loading="mcpTestingName === srv.name"
            title="测试连接"
            @click.stop="handleTestMcpServer(srv.name)"
          />
          <el-checkbox
            :model-value="isMcpSelected(srv.name)"
            @change="toggleMcpServer(srv.name)"
          />
        </div>
      </div>
    </div>

    <div v-if="userMcpServers.length > 0" class="tool-group">
      <div class="tool-group-label">自定义</div>
      <div
        v-for="srv in userMcpServers"
        :key="srv.name"
        :class="['tool-item', { selected: isMcpSelected(srv.name) }]"
      >
        <div class="tool-info">
          <span class="tool-name">
            {{ srv.name }}
            <el-tag size="small" type="success" effect="plain">{{ srv.transport }}</el-tag>
          </span>
          <span v-if="srv.description" class="tool-desc">{{ srv.description }}</span>
          <div v-if="mcpTestResult && mcpTestResult.name === srv.name" class="test-result-inline" :class="mcpTestResult.success ? 'success' : 'error'">
            <el-icon v-if="mcpTestResult.success"><Check /></el-icon>
            <el-icon v-else><Warning /></el-icon>
            <span>{{ mcpTestResult.message }}</span>
          </div>
        </div>
        <div class="tool-actions">
          <el-button
            :icon="VideoPlay"
            size="small"
            circle
            :loading="mcpTestingName === srv.name"
            title="测试连接"
            @click.stop="handleTestMcpServer(srv.name)"
          />
          <el-button
            :icon="Edit"
            size="small"
            circle
            text
            type="primary"
            title="编辑"
            @click.stop="handleEditMcpServer(srv)"
          />
          <el-button
            :icon="Delete"
            size="small"
            circle
            text
            type="danger"
            title="删除"
            @click.stop="handleDeleteMcpServer(srv.name)"
          />
          <el-checkbox
            :model-value="isMcpSelected(srv.name)"
            @change="toggleMcpServer(srv.name)"
          />
        </div>
      </div>
    </div>
  </div>

  <McpUploadDialog
    v-model="showMcpUploadDialog"
    :editing-server="editingMcpServer"
    @success="handleMcpAddSuccess"
  />
</template>

<style scoped>
.test-result-inline {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  margin-top: 2px;
}

.test-result-inline.success {
  color: var(--el-color-success);
}

.test-result-inline.error {
  color: var(--el-color-danger);
}
</style>
