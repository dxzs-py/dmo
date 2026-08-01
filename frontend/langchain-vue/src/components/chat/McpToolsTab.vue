<script setup>
import { ref, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Edit, Delete, Refresh, Check, Warning, VideoPlay,
} from '@element-plus/icons-vue'
import { toolsAPI } from '../../api'
import { useToolsStore } from '../../stores/tools'

const props = defineProps({
  selected: { type: Array, required: true },
  withPopoverLock: { type: Function, required: true },
})

const emit = defineEmits({
  'update:selected': (val) => Array.isArray(val),
  edit: () => true,
})

const toolsStore = useToolsStore()

// ── 本地可写代理：将 props.selected 转为可写形式（v-model 透传） ──
const selectedLocal = computed({
  get: () => props.selected,
  set: (val) => emit('update:selected', val),
})

// ── 数据（从 store 读取） ──
const mcpServers = computed(() => toolsStore.mcpServers)
const mcpLoading = computed(() => toolsStore.mcpLoading)

// ── Computeds ──
const systemMcpServers = computed(() => toolsStore.systemMcpServers)
const userMcpServers = computed(() => toolsStore.userMcpServers)

// ── 数据获取（委托给 store） ──
const fetchMcpServers = (force = false) => toolsStore.loadMcpServers(force)

// ── 选择操作 ──
const isMcpSelected = (name) => selectedLocal.value.includes(name)

const toggleMcpServer = (name) => {
  const idx = selectedLocal.value.indexOf(name)
  if (idx >= 0) {
    selectedLocal.value = selectedLocal.value.filter(n => n !== name)
  } else {
    selectedLocal.value = [...selectedLocal.value, name]
  }
}

const selectAllMcp = () => {
  selectedLocal.value = mcpServers.value.map(s => s.name)
}

const clearMcp = () => {
  selectedLocal.value = []
}

// ── MCP Server 操作 ──
const handleDeleteMcpServer = (name) => props.withPopoverLock(async () => {
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
    selectedLocal.value = selectedLocal.value.filter(n => n !== name)
    toolsStore.invalidateMcpServers()
    fetchMcpServers()
  } catch (e) {
    ElMessage.error('删除失败: ' + (e.response?.data?.message || e.message))
  }
})

// ── 连接测试状态 ──
const mcpTestingName = ref(null)
const mcpTestResult = ref(null)

const handleTestMcpServer = (name) => props.withPopoverLock(async () => {
  mcpTestingName.value = name
  mcpTestResult.value = null
  try {
    const res = await toolsAPI.testMcpServer(name)
    const data = res.data?.data || res.data
    if (data.connected) {
      mcpTestResult.value = { name, success: true, message: `连接成功，共 ${data.tool_count} 个工具` }
    } else {
      mcpTestResult.value = { name, success: false, message: `连接失败${data.error ? '：' + data.error : ''}` }
    }
  } catch (e) {
    mcpTestResult.value = { name, success: false, message: '连接测试失败: ' + (e.response?.data?.message || e.message) }
  } finally {
    mcpTestingName.value = null
  }
})

// ── 编辑（委托给父组件打开对话框） ──
const handleEditMcpServer = (srv) => {
  emit('edit', srv)
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
</template>

<style scoped>
/* 测试连接结果内联显示（仅 MCP Tab 使用） */
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
