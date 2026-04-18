<template>
  <div class="permission-manager">
    <div class="permission-header">
      <h4>工具权限设置</h4>
      <el-select
        v-model="currentSessionMode"
        size="small"
        @change="handleSessionModeChange"
        style="width: 200px"
      >
        <el-option
          v-for="(info, mode) in sessionModes"
          :key="mode"
          :label="modeLabel(mode)"
          :value="mode"
        />
      </el-select>
    </div>

    <div class="mode-description">
      {{ sessionModes[currentSessionMode]?.description }}
    </div>

    <div class="tool-permissions">
      <div
        v-for="(toolInfo, toolName) in toolPermissions"
        :key="toolName"
        class="tool-permission-item"
      >
        <div class="tool-info">
          <span class="tool-name">{{ formatToolName(toolName) }}</span>
          <el-tag
            :type="toolInfo.allowed ? 'success' : 'danger'"
            size="small"
          >
            {{ toolInfo.allowed ? '允许' : '拒绝' }}
          </el-tag>
        </div>
        <el-select
          :model-value="toolInfo.permission"
          size="small"
          style="width: 120px"
          @change="(val) => handleToolPermissionChange(toolName, val)"
        >
          <el-option label="允许" value="allow" />
          <el-option label="拒绝" value="deny" />
          <el-option label="询问" value="prompt" />
        </el-select>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue'
import { chatAPI } from '@/api/client'
import { ElMessage } from 'element-plus'

const props = defineProps({
  sessionId: {
    type: String,
    default: null,
  },
})

const currentSessionMode = ref('workspace-write')
const sessionModes = ref({})
const toolPermissions = ref({})

const modeLabels = {
  'read-only': '🔒 只读模式',
  'workspace-write': '📝 工作区模式',
  'danger-full-access': '⚠️ 完全访问',
}

function modeLabel(mode) {
  return modeLabels[mode] || mode
}

function formatToolName(name) {
  return name.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

async function loadPermissions() {
  try {
    const res = await chatAPI.getPermissions(props.sessionId)
    if (res.data?.code === 200) {
      const data = res.data.data
      currentSessionMode.value = data.policy?.sessionMode || 'workspace-write'
      sessionModes.value = data.sessionModes || {}
      toolPermissions.value = data.tools || {}
    }
  } catch (e) {
    console.error('加载权限失败:', e)
  }
}

async function handleSessionModeChange(mode) {
  try {
    const res = await chatAPI.updatePermissions({
      session_mode: mode,
      session_id: props.sessionId,
    })
    if (res.data?.code === 200) {
      const data = res.data.data
      toolPermissions.value = data.tools || {}
      currentSessionMode.value = data.policy?.sessionMode || mode
      ElMessage.success('权限模式已更新')
    }
  } catch (e) {
    ElMessage.error('更新权限模式失败')
  }
}

async function handleToolPermissionChange(toolName, permission) {
  try {
    const res = await chatAPI.updatePermissions({
      tool_permissions: { [toolName]: permission },
      session_id: props.sessionId,
    })
    if (res.data?.code === 200) {
      const data = res.data.data
      toolPermissions.value = data.tools || {}
      ElMessage.success('工具权限已更新')
    }
  } catch (e) {
    ElMessage.error('更新工具权限失败')
  }
}

onMounted(() => {
  loadPermissions()
})
</script>

<style scoped>
.permission-manager {
  padding: 16px;
}

.permission-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.permission-header h4 {
  margin: 0;
  font-size: 14px;
}

.mode-description {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  margin-bottom: 16px;
  padding: 8px 12px;
  background: var(--el-bg-color-page);
  border-radius: 6px;
}

.tool-permissions {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 400px;
  overflow-y: auto;
}

.tool-permission-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  border-radius: 6px;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
}

.tool-info {
  display: flex;
  align-items: center;
  gap: 8px;
}

.tool-name {
  font-size: 13px;
  font-weight: 500;
}
</style>
