<script setup>
import { computed } from 'vue'
import { ArrowRight, CircleCheck, Close, Loading } from '@element-plus/icons-vue'

const props = defineProps({
  name: {
    type: String,
    required: true
  },
  description: {
    type: String,
    default: ''
  },
  status: {
    type: String,
    default: 'pending',
    validator: (v) => ['pending', 'running', 'completed', 'failed'].includes(v)
  },
  input: {
    type: [Object, String],
    default: null
  },
  output: {
    type: [Object, String],
    default: null
  }
})

const statusIcon = computed(() => {
  switch (props.status) {
    case 'completed': return CircleCheck
    case 'failed': return Close
    case 'running': return Loading
    default: return ArrowRight
  }
})

const statusText = computed(() => {
  switch (props.status) {
    case 'completed': return '已完成'
    case 'failed': return '失败'
    case 'running': return '执行中'
    default: return '待执行'
  }
})

const statusType = computed(() => {
  switch (props.status) {
    case 'completed': return 'success'
    case 'failed': return 'danger'
    case 'running': return 'warning'
    default: return 'info'
  }
})

const formatContent = (content) => {
  if (!content) return ''
  if (typeof content === 'object') {
    try {
      return JSON.stringify(content, null, 2)
    } catch {
      return String(content)
    }
  }
  return String(content)
}
</script>

<template>
  <div :class="['ai-tool', `ai-tool--${status}`]">
    <div class="ai-tool__header">
      <div class="ai-tool__info">
        <el-icon :class="['ai-tool__icon', `is-${status}`]">
          <component :is="statusIcon" :class="{ 'is-loading': status === 'running' }" />
        </el-icon>
        <span class="ai-tool__name">{{ name }}</span>
        <el-tag :type="statusType" size="small">{{ statusText }}</el-tag>
      </div>
    </div>

    <div v-if="description" class="ai-tool__description">
      {{ description }}
    </div>

    <div v-if="input" class="ai-tool__section">
      <div class="ai-tool__section-title">输入参数</div>
      <pre class="ai-tool__section-content">{{ formatContent(input) }}</pre>
    </div>

    <div v-if="output" class="ai-tool__section">
      <div class="ai-tool__section-title">输出结果</div>
      <pre class="ai-tool__section-content">{{ formatContent(output) }}</pre>
    </div>
  </div>
</template>

<style scoped>
.ai-tool {
  background-color: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 12px;
  margin: 8px 0;
}

.ai-tool--completed {
  border-left: 3px solid var(--el-color-success);
}

.ai-tool--failed {
  border-left: 3px solid var(--el-color-danger);
}

.ai-tool--running {
  border-left: 3px solid var(--el-color-warning);
}

.ai-tool__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.ai-tool__info {
  display: flex;
  align-items: center;
  gap: 8px;
}

.ai-tool__icon {
  font-size: 16px;
}

.ai-tool__icon.is-completed {
  color: var(--el-color-success);
}

.ai-tool__icon.is-failed {
  color: var(--el-color-danger);
}

.ai-tool__icon.is-running {
  color: var(--el-color-warning);
}

.ai-tool__icon.is-pending {
  color: var(--el-color-info);
}

.ai-tool__name {
  font-weight: 600;
  font-size: 14px;
  color: var(--el-text-color-primary);
}

.ai-tool__description {
  margin-top: 8px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.ai-tool__section {
  margin-top: 12px;
}

.ai-tool__section-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
  margin-bottom: 6px;
  text-transform: uppercase;
}

.ai-tool__section-content {
  background-color: var(--el-bg-color);
  padding: 8px 12px;
  border-radius: 4px;
  font-size: 12px;
  font-family: 'Consolas', 'Monaco', monospace;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
