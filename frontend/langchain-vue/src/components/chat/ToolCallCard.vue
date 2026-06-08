<script setup>
import { ref, computed } from 'vue'
import { ArrowDown, ArrowRight, CircleCheck, Close, Loading, MagicStick } from '@element-plus/icons-vue'

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
    type: [Object, String, Array],
    default: null
  },
  output: {
    type: [Object, String, Array],
    default: null
  },
  status: {
    type: String,
    default: 'pending',
    validator: (value) => ['pending', 'running', 'completed', 'failed', 'pending_approval'].includes(value)
  }
})

// 判断是否为 Skill 调用
const isSkillCall = computed(() => props.toolName.startsWith('skill_'))

// 判断是否为知识库检索工具
const isKnowledgeBase = computed(() => props.toolName.startsWith('knowledge_base_'))

// 知识库工具默认折叠
const isExpanded = ref(isKnowledgeBase.value ? false : true)

// Skill 模式标签
const skillModeLabel = computed(() => {
  if (!isSkillCall.value) return ''
  const inputObj = typeof props.input === 'string' ? (() => { try { return JSON.parse(props.input) } catch { return {} } })() : (props.input || {})
  const mode = inputObj.mode || inputObj._mode
  if (mode === 'hybrid') return '混合'
  if (mode === 'advisor') return '顾问'
  return '管线'
})

const hybridSections = computed(() => {
  if (!isSkillCall.value || typeof props.output !== 'string') return null
  if (skillModeLabel.value !== '混合') return null
  return {
    '执行结果': props.output,
  }
})

const statusIcon = computed(() => {
  switch (props.status) {
    case 'completed':
      return CircleCheck
    case 'failed':
      return Close
    case 'running':
      return Loading
    case 'pending_approval':
      return ArrowRight
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
    case 'pending_approval':
      return 'info'
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
    case 'pending_approval':
      return '待审批'
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
  <div :class="['tool-call-card', `tool-call-card--${status}`, { 'tool-call-card--skill': isSkillCall }]">
    <div class="tool-call-header" @click="isExpanded = !isExpanded">
      <div class="tool-call-left">
        <el-icon class="status-icon" :class="`status-${status}`">
          <component :is="isSkillCall ? MagicStick : statusIcon" :class="{ 'is-loading': status === 'running' }" />
        </el-icon>
        <div class="tool-info">
          <span class="tool-name">{{ toolName }}</span>
          <el-tag v-if="isSkillCall && skillModeLabel" size="small" :type="skillModeLabel === '管线' ? 'primary' : skillModeLabel === '顾问' ? 'success' : 'warning'" effect="plain">{{ skillModeLabel }}</el-tag>
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
      <!-- Skill hybrid 模式分区渲染 -->
      <template v-if="hybridSections">
        <div class="tool-call-section">
          <div class="section-title">执行结果</div>
          <pre class="section-content">{{ hybridSections['执行结果'] || '' }}</pre>
        </div>
      </template>
      <template v-else-if="isSkillCall && output && skillModeLabel === '顾问'">
        <div class="tool-call-section">
          <div class="section-title">技能确认</div>
          <div class="section-content">{{ formatContent(output) }}</div>
        </div>
      </template>
      <!-- 普通输出 -->
      <template v-else>
        <div v-if="output" class="tool-call-section">
          <div class="section-title">{{ isKnowledgeBase ? '检索摘要' : '输出' }}</div>
          <pre v-if="isKnowledgeBase" class="section-content section-content--compact">{{ formatContent(output) }}</pre>
          <pre v-else class="section-content">{{ formatContent(output) }}</pre>
        </div>
      </template>
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

.tool-call-card--skill {
  border-left: 3px solid var(--el-color-primary);
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

.section-content--markdown {
  white-space: pre-wrap;
  font-family: inherit;
  line-height: 1.7;
}

.section-content--compact {
  max-height: 120px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
