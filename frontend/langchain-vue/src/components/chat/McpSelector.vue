<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Connection, Plus, Delete, Refresh, VideoPlay } from '@element-plus/icons-vue'
import { chatAPI } from '../../api'

const props = defineProps({
  modelValue: {
    type: Array,
    default: () => [],
  },
  enabled: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits({
  'update:modelValue': (val) => Array.isArray(val),
  'update:enabled': (val) => typeof val === 'boolean',
  change: () => true,
})

const servers = ref([])
const loading = ref(false)
const showAddDialog = ref(false)

const selectedServers = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val),
})

const enabled = computed({
  get: () => props.enabled,
  set: (val) => emit('update:enabled', val),
})

const systemServers = computed(() => servers.value.filter(s => s.source === 'system'))
const userServers = computed(() => servers.value.filter(s => s.source === 'user'))

const fetchServers = async () => {
  loading.value = true
  try {
    const res = await chatAPI.getMcpServers()
    const data = res.data?.data || res.data
    servers.value = data.servers || []
  } catch (e) {
    servers.value = []
  } finally {
    loading.value = false
  }
}

const toggleServer = (serverName) => {
  const idx = selectedServers.value.indexOf(serverName)
  if (idx >= 0) {
    selectedServers.value = selectedServers.value.filter(n => n !== serverName)
  } else {
    selectedServers.value = [...selectedServers.value, serverName]
  }
  emit('change')
}

const isSelected = (name) => selectedServers.value.includes(name)

const handleDeleteServer = async (name) => {
  try {
    await chatAPI.deleteMcpServer(name)
    ElMessage.success(`MCP Server "${name}" 已删除`)
    servers.value = servers.value.filter(s => s.name !== name)
    selectedServers.value = selectedServers.value.filter(n => n !== name)
    emit('change')
  } catch (e) {
    ElMessage.error('删除失败: ' + (e.response?.data?.message || e.message))
  }
}

const handleButtonClick = () => {
  if (!props.enabled) {
    enabled.value = true
    ElMessage.success('已开启 MCP 工具')
  }
}

const handleTestServer = async (name) => {
  try {
    ElMessage.info(`正在测试 "${name}" 连接...`)
    const res = await chatAPI.testMcpServer(name)
    const data = res.data?.data || res.data
    if (data.connected) {
      ElMessage.success(`"${name}" 连接成功，共 ${data.tool_count} 个工具`)
    } else {
      ElMessage.warning(`"${name}" 连接失败`)
    }
  } catch (e) {
    ElMessage.error('连接测试失败: ' + (e.response?.data?.message || e.message))
  }
}

const handleAddSuccess = () => {
  showAddDialog.value = false
  fetchServers()
  emit('change')
}

watch(() => props.enabled, (val) => {
  if (val && servers.value.length === 0) {
    fetchServers()
  }
})

onMounted(() => {
  if (props.enabled) {
    fetchServers()
  }
})
</script>

<template>
  <el-popover
    placement="top-start"
    :width="360"
    trigger="click"
    :disabled="!enabled"
    @show="fetchServers"
  >
    <template #reference>
      <button
        class="toolbar-btn"
        :class="{ active: enabled }"
        :title="enabled ? 'MCP 工具设置' : '开启 MCP 工具'"
        @click="handleButtonClick"
      >
        <el-icon :size="18"><Connection /></el-icon>
        <span v-if="enabled && selectedServers.length > 0" class="mcp-badge">{{ selectedServers.length }}</span>
      </button>
    </template>

    <div class="mcp-selector">
      <div class="mcp-selector-header">
        <span class="mcp-selector-title">MCP 服务器</span>
        <div class="mcp-selector-actions">
          <el-switch
            :model-value="enabled"
            size="small"
            active-text="开"
            inactive-text="关"
            @change="(val) => { enabled = val; if (!val) ElMessage.info('已关闭 MCP 工具') }"
          />
          <el-button
            :icon="Refresh"
            size="small"
            circle
            :loading="loading"
            @click="fetchServers"
          />
          <el-button
            :icon="Plus"
            size="small"
            circle
            type="primary"
            @click="showAddDialog = true"
          />
        </div>
      </div>

      <div v-if="servers.length === 0 && !loading" class="mcp-empty">
        <span>暂无 MCP 服务器</span>
        <el-button type="primary" size="small" @click="showAddDialog = true">
          添加服务器
        </el-button>
      </div>

      <div v-else class="mcp-server-list">
        <div v-if="systemServers.length > 0" class="mcp-group">
          <div class="mcp-group-label">系统内置</div>
          <div
            v-for="srv in systemServers"
            :key="srv.name"
            :class="['mcp-server-item', { selected: isSelected(srv.name) }]"
            @click="toggleServer(srv.name)"
          >
            <div class="mcp-server-info">
              <div class="mcp-server-name-row">
                <span class="mcp-server-name">{{ srv.name }}</span>
                <el-tag size="small" type="info" effect="plain">{{ srv.transport }}</el-tag>
              </div>
              <div v-if="srv.description" class="mcp-server-desc">{{ srv.description }}</div>
            </div>
            <div class="mcp-server-actions" @click.stop>
              <el-button
                :icon="VideoPlay"
                size="small"
                circle
                title="测试连接"
                @click="handleTestServer(srv.name)"
              />
              <el-checkbox
                :model-value="isSelected(srv.name)"
                @change="toggleServer(srv.name)"
              />
            </div>
          </div>
        </div>

        <div v-if="userServers.length > 0" class="mcp-group">
          <div class="mcp-group-label">自定义</div>
          <div
            v-for="srv in userServers"
            :key="srv.name"
            :class="['mcp-server-item', { selected: isSelected(srv.name) }]"
            @click="toggleServer(srv.name)"
          >
            <div class="mcp-server-info">
              <div class="mcp-server-name-row">
                <span class="mcp-server-name">{{ srv.name }}</span>
                <el-tag size="small" type="success" effect="plain">{{ srv.transport }}</el-tag>
              </div>
              <div v-if="srv.description" class="mcp-server-desc">{{ srv.description }}</div>
            </div>
            <div class="mcp-server-actions" @click.stop>
              <el-button
                :icon="VideoPlay"
                size="small"
                circle
                title="测试连接"
                @click="handleTestServer(srv.name)"
              />
              <el-button
                :icon="Delete"
                size="small"
                circle
                type="danger"
                title="删除"
                @click="handleDeleteServer(srv.name)"
              />
              <el-checkbox
                :model-value="isSelected(srv.name)"
                @change="toggleServer(srv.name)"
              />
            </div>
          </div>
        </div>
      </div>

      <div v-if="selectedServers.length > 0" class="mcp-selector-footer">
        已选择 {{ selectedServers.length }} 个服务器
      </div>
    </div>

    <McpUploadDialog
      v-model="showAddDialog"
      @success="handleAddSuccess"
    />
  </el-popover>
</template>

<style scoped>
.toolbar-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--muted-foreground);
  cursor: pointer;
  transition: all var(--transition-fast);
  position: relative;
}

.toolbar-btn:hover:not(:disabled) {
  background: var(--accent);
  color: var(--foreground);
}

.toolbar-btn.active {
  color: var(--sidebar-primary);
  background: color-mix(in srgb, var(--sidebar-primary) 10%, transparent);
}

.mcp-badge {
  position: absolute;
  top: -2px;
  right: -2px;
  min-width: 16px;
  height: 16px;
  border-radius: 8px;
  background: var(--sidebar-primary);
  color: white;
  font-size: 10px;
  line-height: 16px;
  text-align: center;
  padding: 0 4px;
}

.mcp-selector {
  max-height: 400px;
  overflow-y: auto;
}

.mcp-selector-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.mcp-selector-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--foreground);
}

.mcp-selector-actions {
  display: flex;
  gap: 4px;
}

.mcp-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 20px 0;
  font-size: 13px;
  color: var(--muted-foreground);
}

.mcp-server-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.mcp-group {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.mcp-group-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--muted-foreground);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  padding: 4px 0;
}

.mcp-server-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 10px;
  border-radius: 8px;
  border: 1px solid var(--border);
  cursor: pointer;
  transition: all var(--transition-fast);
}

.mcp-server-item:hover {
  border-color: var(--sidebar-primary);
  background: var(--accent);
}

.mcp-server-item.selected {
  border-color: var(--sidebar-primary);
  background: color-mix(in srgb, var(--sidebar-primary) 6%, transparent);
}

.mcp-server-info {
  flex: 1;
  min-width: 0;
}

.mcp-server-name-row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.mcp-server-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--foreground);
}

.mcp-server-desc {
  font-size: 11px;
  color: var(--muted-foreground);
  margin-top: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mcp-server-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.mcp-selector-footer {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--border);
  font-size: 12px;
  color: var(--sidebar-primary);
  text-align: center;
}
</style>
