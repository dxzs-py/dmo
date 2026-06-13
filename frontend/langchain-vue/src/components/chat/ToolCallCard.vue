<script setup>
import { ref, computed } from 'vue'
import { ArrowDown, ArrowRight, CircleCheck, Close, Loading, MagicStick } from '@element-plus/icons-vue'
import { ToolCallStatus } from '../../types'

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
    validator: (value) => Object.values(ToolCallStatus).includes(value)
  },
  toolCall: {
    type: Object,
    default: null
  }
})

const emit = defineEmits(['approve', 'reject'])

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
    case 'approved':
      return CircleCheck
    case 'failed':
    case 'rejected':
      return Close
    case 'running':
    case 'processing':
      return Loading
    case 'pending_approval':
      return ArrowRight
    case 'timeout':
      return Close
    default:
      return ArrowRight
  }
})

const statusType = computed(() => {
  switch (props.status) {
    case 'completed':
    case 'approved':
      return 'success'
    case 'failed':
    case 'rejected':
      return 'danger'
    case 'running':
    case 'processing':
      return 'warning'
    case 'pending_approval':
      return 'info'
    case 'timeout':
      return 'danger'
    default:
      return 'info'
  }
})

const statusText = computed(() => {
  switch (props.status) {
    case 'completed':
      return '已完成'
    case 'approved':
      return '已确认'
    case 'failed':
      return '失败'
    case 'rejected':
      return '已拒绝'
    case 'running':
      return '执行中'
    case 'processing':
      return '处理中'
    case 'pending_approval':
      return '待审批'
    case 'timeout':
      return '已超时'
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

// 审批相关
const approvalData = computed(() => props.toolCall?.approval || null)

const isPendingApproval = computed(() => props.status === ToolCallStatus.PENDING_APPROVAL && approvalData.value)

const dangerLevel = computed(() => approvalData.value?.danger_level || 'low')

const dangerLevelLabel = computed(() => {
  const map = { low: '低风险', medium: '中风险', high: '高风险' }
  return map[dangerLevel.value] || '低风险'
})

const dangerLevelTagType = computed(() => {
  const map = { low: 'primary', medium: 'warning', high: 'danger' }
  return map[dangerLevel.value] || 'primary'
})

const approvalBorderColor = computed(() => {
  if (props.status !== 'pending_approval') return ''
  const map = {
    medium: 'var(--el-color-warning)',
    high: 'var(--el-color-danger)',
    low: 'var(--el-color-primary)'
  }
  return map[dangerLevel.value] || ''
})

const approvalInputValue = ref('')

const isConfirmWithInput = computed(() => approvalData.value?.action === 'confirm_with_input')

// 审批操作展示
const operationText = computed(() => approvalData.value?.operation || approvalData.value?.command || '')
const operationLabel = computed(() => {
  const map = {
    shell_exec: '命令',
    fs_write_file: '文件路径',
    file_reader: '文件路径',
    agent_cleanup: '操作',
  }
  return map[approvalData.value?.tool_name] || '操作'
})
</script>

<template>
  <div
    :class="['tool-call-card', `tool-call-card--${status}`, { 'tool-call-card--skill': isSkillCall }]"
    :style="approvalBorderColor ? { borderColor: approvalBorderColor } : {}"
  >
    <div class="tool-call-header" @click="isExpanded = !isExpanded">
      <div class="tool-call-left">
        <el-icon class="status-icon" :class="`status-${status}`">
          <component :is="isSkillCall ? MagicStick : statusIcon" :class="{ 'is-loading': status === ToolCallStatus.RUNNING || status === ToolCallStatus.PROCESSING }" />
        </el-icon>
        <div class="tool-info">
          <span class="tool-name">{{ toolName }}</span>
          <el-tag v-if="isSkillCall && skillModeLabel" size="small" :type="skillModeLabel === '管线' ? 'primary' : skillModeLabel === '顾问' ? 'success' : 'warning'" effect="plain">{{ skillModeLabel }}</el-tag>
          <el-tag v-if="status === ToolCallStatus.APPROVED" size="small" type="success">已确认</el-tag>
          <el-tag v-else-if="status === ToolCallStatus.REJECTED" size="small" type="danger">已拒绝</el-tag>
          <el-tag v-else :type="statusType" size="small">{{ statusText }}</el-tag>
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

      <!-- 审批面板 -->
      <div v-if="isPendingApproval" class="approval-panel">
        <div class="approval-panel__header">
          <span class="approval-panel__title">审批确认</span>
          <el-tag size="small" :type="dangerLevelTagType" effect="dark">{{ dangerLevelLabel }}</el-tag>
        </div>
        <div v-if="approvalData.title" class="approval-panel__title-text">{{ approvalData.title }}</div>
        <div v-if="approvalData.description" class="approval-panel__desc">{{ approvalData.description }}</div>
        <div v-if="operationText" class="approval-panel__command">
          <span class="approval-panel__command-label">{{ operationLabel }}</span>
          <pre class="approval-panel__code">{{ operationText }}</pre>
        </div>
        <div v-if="isConfirmWithInput" class="approval-panel__input">
          <el-input
            v-model="approvalInputValue"
            :placeholder="approvalData.input_placeholder || '请输入值...'"
            size="small"
            clearable
            @keyup.enter="emit('approve', { ...toolCall, _user_input: approvalInputValue })"
          />
        </div>
        <div class="approval-panel__actions">
          <el-button type="danger" size="small" @click.stop="emit('reject', toolCall)">拒绝</el-button>
          <el-button
            type="primary"
            size="small"
            @click.stop="emit('approve', isConfirmWithInput ? { ...toolCall, _user_input: approvalInputValue } : toolCall)"
          >
            {{ isConfirmWithInput ? '确认并提交' : '确认执行' }}
          </el-button>
        </div>
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

.tool-call-card--pending_approval {
  border-left: 3px solid var(--el-color-info);
}

.tool-call-card--approved {
  border-left: 3px solid var(--el-color-success);
}

.tool-call-card--rejected {
  border-left: 3px solid var(--el-color-danger);
}

.tool-call-card--timeout {
  border-left: 3px solid var(--el-color-danger);
}

.tool-call-card--processing {
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

.status-icon.status-pending_approval {
  color: var(--el-color-info);
}

.status-icon.status-approved {
  color: var(--el-color-success);
}

.status-icon.status-rejected {
  color: var(--el-color-danger);
}

/* 审批面板 */
.approval-panel {
  margin-top: 12px;
  padding: 12px;
  background-color: var(--el-fill-color-lighter);
  border-radius: 6px;
  border: 1px solid var(--el-border-color-light);
}

.approval-panel__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.approval-panel__title {
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.approval-panel__desc {
  font-size: 13px;
  color: var(--el-text-color-secondary);
  margin-bottom: 8px;
  line-height: 1.5;
}

.approval-panel__command {
  margin-bottom: 12px;
}

.approval-panel__command-label {
  display: inline-block;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-bottom: 4px;
}

.approval-panel__code {
  background-color: var(--el-bg-color-page);
  padding: 10px 12px;
  border-radius: 4px;
  font-family: 'Courier New', Consolas, monospace;
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
  margin: 0;
  color: var(--el-text-color-primary);
}

.approval-panel__actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.approval-panel__title-text {
  font-size: 14px;
  font-weight: 600;
  color: var(--el-text-color-primary);
  margin-bottom: 6px;
}

.approval-panel__input {
  margin-bottom: 10px;
}
</style>
