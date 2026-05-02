<script setup>
import { ref, computed } from 'vue'
import { ArrowDown, ArrowRight, CircleCheck, Close, Loading } from '@element-plus/icons-vue'

const props = defineProps({
  toolName: {
    type: String,
    required: true
  },
  description: {
    type: String,
    default: ''
  },
  input: {
    type: [Object, String],
    default: null
  },
  output: {
    type: [Object, String],
    default: null
  },
  status: {
    type: String,
    default: 'pending',
    validator: (value) => ['pending', 'running', 'completed', 'failed'].includes(value)
  }
})

const isExpanded = ref(true)

const statusIcon = computed(() => {
  switch (props.status) {
    case 'completed':
      return CircleCheck
    case 'failed':
      return Close
    case 'running':
      return Loading
    default:
      return ArrowRight
  }
})

const statusType = computed(() => {
  switch (props.status) {
    case 'completed':
      return 'success'
    case 'failed':
      return 'danger'
    case 'running':
      return 'warning'
    default:
      return 'info'
  }
})

const statusText = computed(() => {
  switch (props.status) {
    case 'completed':
      return '已完成'
    case 'failed':
      return '失败'
    case 'running':
      return '执行中'
    default:
      return '待执行'
  }
})

const formatContent = (content) => {
  if (!content) return ''
  if (typeof content === 'object') {
    return JSON.stringify(content, null, 2)
  }
  return String(content)
}
</script>

<template>
  <div :class="['tool-call-card', `tool-call-card--${status}`]">
    <div class="tool-call-header" @click="isExpanded = !isExpanded">
      <div class="tool-call-left">
        <el-icon class="status-icon" :class="`status-${status}`">
          <component :is="statusIcon" :class="{ 'is-loading': status === 'running' }" />
        </el-icon>
        <div class="tool-info">
          <span class="tool-name">{{ toolName }}</span>
          <el-tag :type="statusType" size="small">{{ statusText }}</el-tag>
        </div>
      </div>
      <el-icon class="expand-icon" :class="{ 'rotated': isExpanded }">
        <ArrowDown />
      </el-icon>
    </div>
    
    <div v-if="description" class="tool-call-description">{{ description }}</div>

    <div v-if="isExpanded" class="tool-call-content">
      <div v-if="input" class="tool-call-section">
        <div class="section-title">输入</div>
        <pre class="section-content">{{ formatContent(input) }}</pre>
      </div>
      <div v-if="output" class="tool-call-section">
        <div class="section-title">输出</div>
        <pre class="section-content">{{ formatContent(output) }}</pre>
      </div>
    </div>
  </div>
</template>

<style scoped>
.tool-call-card {
  background-color: var(--el-bg-color-page);
  border: 1px solid var(--el-border-color);
  border-radius: 8px;
  margin: 8px 0;
  overflow: hidden;
}

.tool-call-card--completed {
  border-left: 3px solid var(--el-color-success);
}

.tool-call-card--failed {
  border-left: 3px solid var(--el-color-danger);
}

.tool-call-card--running {
  border-left: 3px solid var(--el-color-warning);
}

.tool-call-description {
  padding: 0 16px 8px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.tool-call-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  cursor: pointer;
  transition: background-color 0.2s;
}

.tool-call-header:hover {
  background-color: var(--el-fill-color-light);
}

.tool-call-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.status-icon {
  font-size: 18px;
}

.status-icon.status-completed {
  color: var(--el-color-success);
}

.status-icon.status-failed {
  color: var(--el-color-danger);
}

.status-icon.status-running {
  color: var(--el-color-warning);
  animation: pulse 1.5s infinite;
}

.status-icon .is-loading {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

.status-icon.status-pending {
  color: var(--el-color-info);
}

.tool-info {
  display: flex;
  align-items: center;
  gap: 8px;
}

.tool-name {
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.expand-icon {
  color: var(--el-text-color-secondary);
  transition: transform 0.2s;
}

.expand-icon.rotated {
  transform: rotate(180deg);
}

.tool-call-content {
  padding: 0 16px 16px;
}

.tool-call-section {
  margin-top: 12px;
}

.section-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 8px;
}

.section-content {
  background-color: var(--el-fill-color-lighter);
  padding: 12px;
  border-radius: 6px;
  font-family: 'Courier New', monospace;
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
  margin: 0;
  color: var(--el-text-color-primary);
  max-height: 200px;
  overflow-y: auto;
}
</style>
