<script setup>
import { ref, computed, watch } from 'vue'
import { ArrowDown, ArrowRight, CircleCheck, Clock, Close, Connection, Loading, MagicStick, Promotion } from '@element-plus/icons-vue'
import { ToolCallStatus } from '../../types'
import {
  formatToolParameters,
  formatToolResult,
  formatToolSummaryLine,
} from '../../utils/toolAdapters'

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
  },
  // 该工具触发了嵌套子代理（异步调用子代理，Task 4.4）：
  // 与子代理内部工具（isSubagentCall）区分，展示独立视觉样式 + 「子代理」标签
  isSubagentTrigger: {
    type: Boolean,
    default: false
  },
  // 审批控件置灰（Agent 图层嵌套规范 Task 6.4）：
  // 仅「waiting + 活跃版本 + 未固化 + 末尾轮次」可交互，其余场景置灰
  // （历史版本 / 已固化 / 非末尾轮次消息上的工具审批按钮禁用）
  approvalDisabled: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['approve', 'reject'])

// 判断是否为 Skill 调用
const isSkillCall = computed(() => props.toolName.startsWith('skill_'))

// 默认折叠为单行摘要（对标 Claude Code / Cursor）：
// 审批中（waiting）与运行中（running）工具展开，其余状态折叠
const isExpanded = ref(
  props.status === ToolCallStatus.WAITING || props.status === ToolCallStatus.RUNNING
)

// 状态驱动展开/折叠：
// 审批中（waiting）与运行中（running）强制展开（审批面板/执行过程需可见）
// 进入终态自动折叠为单行摘要（执行过程用户已看到，折叠减少信息噪音）
// 注意：卡片挂载时 status 可能为 pending，随后事件流更新为 waiting，
// 因此不能只靠初始值，必须在此处对 waiting/running 也做展开处理
watch(() => props.status, (newStatus) => {
  const terminal = newStatus === ToolCallStatus.COMPLETED
    || newStatus === ToolCallStatus.FAILED
    || newStatus === ToolCallStatus.REJECTED
    || newStatus === ToolCallStatus.TIMEOUT
  if (terminal) {
    isExpanded.value = false
  } else if (newStatus === ToolCallStatus.WAITING || newStatus === ToolCallStatus.RUNNING) {
    isExpanded.value = true
  }
})

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
    case 'rejected':
      return Close
    case 'running':
      return Loading
    case 'waiting':
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
      return 'success'
    case 'failed':
    case 'rejected':
      return 'danger'
    case 'running':
      return 'warning'
    case 'waiting':
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
    case 'failed':
      return '失败'
    case 'rejected':
      return '已拒绝'
    case 'running':
      return '执行中'
    case 'waiting':
      return '待审批'
    case 'timeout':
      return '已超时'
    case 'pending':
      return '等待中'
    default:
      return '等待中'
  }
})

// ============================================================
// 工具参数 / 结果格式化（接入 utils/toolAdapters.js）
// ============================================================
// 设计说明：
// ToolCallCard 原先用简陋的 formatContent(content) = JSON.stringify(content, null, 2)
// 统一序列化所有工具的输入/输出，导致 deepagents 原生工具（write_file/read_file/
// edit_file/execute/grep/glob/ls/task 等）缺乏语义化展示：
//   - read_file 结果不按扩展名高亮
//   - edit_file 不以 diff 形式展示
//   - execute 不以命令行 monospace 展示
//   - write_file 不截断 content 预览
//
// 修复：通过 formatToolParameters/formatToolResult 调用 utils/toolAdapters.js 中
// 注册的专用格式化器，按工具名返回 { label, formatted, displayMode } 三元组。
// 非内置工具回退到通用 JSON 序列化（与原 formatContent 行为一致）。
//
// 关键不变量：
//   - displayMode 由格式化器决定，模板按 displayMode 渲染（inline 单行 / pre JSON / pre command）
//   - label 由格式化器决定，覆盖原硬编码 operationLabel map（已含 deepagents 工具名）
//   - props 接口不变，4 个调用方（ChatMessage/AiMessage/ChainOfThought/DeepResearchView）无需修改

const inputDisplay = computed(() => {
  // Skill hybrid 模式保留原 input 直传（hybridSections 分支处理输出）
  if (isSkillCall.value && skillModeLabel.value === '混合') {
    return { label: '输入', formatted: '', displayMode: 'skip' }
  }
  const result = formatToolParameters(props.toolName, props.input)
  // input 为空时 displayMode 设为 skip，模板不渲染输入区
  if (!result.formatted) {
    return { ...result, displayMode: 'skip' }
  }
  return result
})

const outputDisplay = computed(() => {
  // Skill hybrid/advisor 模式由 hybridSections 分支处理
  if (isSkillCall.value && skillModeLabel.value !== '管线') {
    return { label: '输出', formatted: '', displayMode: 'skip', language: 'text' }
  }
  const result = formatToolResult(props.toolName, props.output)
  // output 为空时 displayMode 设为 skip，模板不渲染输出区
  if (!result.formatted) {
    return { ...result, displayMode: 'skip' }
  }
  return result
})

// 输入区 class 计算（移到 computed 避免模板内复杂表达式导致 Vue parser 报错）
const inputContentClass = computed(() => [
  'section-content',
  `display-mode--${inputDisplay.value.displayMode}`,
  { 'section-content--inline': inputDisplay.value.displayMode === 'inline' },
])

// 输出区 class 计算
const outputContentClass = computed(() => [
  'section-content',
  `display-mode--${outputDisplay.value.displayMode}`,
  {
    'section-content--inline': outputDisplay.value.displayMode === 'inline',
    'section-content--monospace': outputDisplay.value.displayMode === 'monospace' || outputDisplay.value.displayMode === 'command',
  },
])

/** 工具是否处于可展示输出的终态（completed/failed/rejected/timeout），
 *  避免审批阶段（pending/waiting/running）提前渲染 result 字段导致显示"已写入"等误导文案。 */
const shouldShowOutput = computed(() => {
  const status = props.status
  return status === ToolCallStatus.COMPLETED
    || status === ToolCallStatus.FAILED
    || status === ToolCallStatus.REJECTED
    || status === ToolCallStatus.TIMEOUT
})

// 审批相关
const approvalData = computed(() => props.toolCall?.approval || null)

// 审批态与工具执行态解耦：tc.approval.state 驱动审批面板，tc.status 驱动工具执行状态。
// isWaiting 同时检查 status（timer 更新）和 approval.state（setApprovalToToolCall 设置）
// 包含 processing 状态：批量审批中单工具先点击"确认执行"后 state 变为 processing，
// 此时审批面板仍需保留（按钮禁用），防止面板突然消失给用户带来困惑
const isWaiting = computed(() => {
  const approval = approvalData.value
  if (!approval) return false
  return props.status === ToolCallStatus.WAITING
    || approval.state === 'pending'
    || approval.state === 'processing'
})

// 审批处理中（已提交确认，等待执行完毕）
const isProcessingApproval = computed(() => approvalData.value?.state === 'processing')

// P19 修复：已审批但等待同批其余工具完成审批（approval.state === 'waiting'）
// 此时审批面板应保留，按钮 disabled，并显示提示文字
// P21 补充：工具进入终态后不可能还在等待同批——终态时等待审批面板应消失
const isWaitingForSiblings = computed(() => {
  if (!approvalData.value || approvalData.value.state !== 'waiting') return false
  // 工具已进入终态：不再显示"等待同批"面板（P21；拒绝/超时同样适用）
  if (props.status === ToolCallStatus.COMPLETED
    || props.status === ToolCallStatus.FAILED
    || props.status === ToolCallStatus.REJECTED
    || props.status === ToolCallStatus.TIMEOUT) return false
  return true
})

// 批量等待时区分"本工具已批准"与"本工具已拒绝"（后端 waiting payload 透传 approved）
const isWaitingForSiblingsRejected = computed(() => {
  return isWaitingForSiblings.value && approvalData.value?.approved === false
})

// riskLevel 是唯一权威风险等级字段（safe/controlled/high）
// 双数据源（根因修复）：
//   1. toolCall.riskLevel（工具事件路径）：后端 publish_tool_call payload 携带 risk_level，
//      toolCallHandler.js 写入 toolCall.riskLevel。所有 tool_call_waiting/running/timeout
//      事件都携带此字段，非触发浏览器通过 WebSocket 收到工具事件即可获取风险等级
//   2. approvalData.riskLevel（审批事件路径）：后端 _build_approval_extra_fields 注入
//      approval_* 事件 payload 顶层，approvalStore.handleApprovalEvent 写入 approval
// 优先级：toolCall.riskLevel > approvalData.riskLevel > 'controlled'（保守策略）
// 根因：原实现仅从 approvalData 读取，approval_pending 事件丢失时 riskLevel 缺失，
// 导致非触发浏览器误显示"安全"（保守回退 'controlled' 避免此问题）
const riskLevel = computed(() => {
  return props.toolCall?.riskLevel
    || approvalData.value?.riskLevel
    || 'controlled'
})

const isHighRisk = computed(() => riskLevel.value === 'high')

const riskLevelLabel = computed(() => {
  const map = { safe: '安全', controlled: '需审批', high: '高危' }
  return map[riskLevel.value] || '需审批'
})

const riskLevelTagType = computed(() => {
  const map = { safe: 'success', controlled: 'warning', high: 'danger' }
  return map[riskLevel.value] || 'warning'
})

// SAFE 级自动通过标记（来自 tool_call 事件 payload 的 auto_approved 字段）
// SAFE 级工具不创建 Approval 记录，由 ApprovalMiddleware._audit_auto_approved_tools
// 注册 auto_approved=True 到 tool_call_lifecycle context 并透传到事件 payload
const isAutoApproved = computed(() => props.toolCall?.isAutoApproved === true)

// 子 agent 嵌套层级（Phase E3）
// 双数据源：toolCall（工具事件路径，覆盖 SAFE 自动通过/子 agent 内部工具调用）
//          + approvalData（审批事件路径，CONTROLLED/HIGH 级审批）
// 优先从 toolCall 读取（工具事件先于审批事件到达，且覆盖非审批场景），
// 回退到 approvalData（审批事件携带的嵌套字段，与 toolCall 一致）
// depth: 0=主 agent, 1=一级子 agent, 2=二级子 agent
const nestingDepth = computed(() => {
  const depth = props.toolCall?.depth ?? approvalData.value?.depth
  return typeof depth === 'number' && depth > 0 ? depth : 0
})

// 完整调用链路（如 ["main", "web-researcher"]）
const agentPath = computed(() => {
  const path = props.toolCall?.agentPath ?? approvalData.value?.agentPath
  return Array.isArray(path) && path.length > 0 ? path : null
})

const agentName = computed(() => props.toolCall?.agentName || approvalData.value?.agentName || '')

// 是否为子 agent 调用（有嵌套层级信息）
const isSubagentCall = computed(() => nestingDepth.value > 0 || !!agentName.value)

// 子 agent 调用链路展示文本（如 "main → web-researcher"）
const agentPathText = computed(() => {
  if (agentPath.value && agentPath.value.length > 0) {
    return agentPath.value.join(' → ')
  }
  return agentName.value || ''
})

const approvalBorderColor = computed(() => {
  if (props.status !== 'waiting') return ''
  // 统一使用 riskLevel 驱动边框颜色（与风险等级标签一致）
  const map = {
    safe: 'var(--el-color-primary)',
    controlled: 'var(--el-color-warning)',
    high: 'var(--el-color-danger)',
  }
  return map[riskLevel.value] || ''
})

const approvalInputValue = ref('')

const isConfirmWithInput = computed(() => approvalData.value?.action === 'confirm_with_input')

// approvalArgs：审批面板统一展示所有用户可见参数
const approvalArgs = computed(() => {
  const args = approvalData.value?.parameters
  if (!args || typeof args !== 'object') return []
  const skipKeys = ['threadId', '_meta', 'timeout', 'encoding']
  return Object.entries(args)
    .filter(([key, val]) => val != null && val !== '' && !skipKeys.includes(key) && !key.startsWith('_'))
    .map(([key, val]) => ({ key, value: String(val) }))
})

// 卡片头部单行摘要（折叠态展示：参数摘要 → 结果摘要）
const summaryLine = computed(() =>
  formatToolSummaryLine(props.toolName, props.input, props.output, props.status)
)
</script>

<template>
  <div
    :class="['tool-call-card', `tool-call-card--${status}`, { 'tool-call-card--skill': isSkillCall, 'tool-call-card--high-risk': isHighRisk, 'tool-call-card--subagent': isSubagentCall, 'tool-call-card--subagent-trigger': isSubagentTrigger }]"
    :style="approvalBorderColor ? { borderColor: approvalBorderColor } : {}"
  >
      <div class="tool-call-header" @click="isExpanded = !isExpanded">
        <div class="tool-call-left">
          <el-icon class="status-icon" :class="`status-${status}`">
            <component :is="isSkillCall ? MagicStick : statusIcon" :class="{ 'is-loading': status === ToolCallStatus.RUNNING }" />
          </el-icon>
          <div class="tool-info">
            <div class="tool-info__row">
              <span :class="['tool-name', { 'tool-name--high-risk': isHighRisk }]">{{ toolName }}</span>
              <!-- 触发嵌套子代理的工具（异步子代理调用，Task 4.4） -->
              <el-tag v-if="isSubagentTrigger" size="small" type="warning" effect="plain" class="subagent-trigger-tag">
                <el-icon class="subagent-trigger-icon"><Promotion /></el-icon>
                子代理
              </el-tag>
              <!-- 子 agent 嵌套链路展示（Phase E3） -->
              <el-tag v-if="isSubagentCall" size="small" type="info" effect="plain" class="agent-path-tag">
                <el-icon class="agent-path-icon"><Connection /></el-icon>
                {{ agentPathText }}
                <span v-if="nestingDepth > 0" class="depth-badge">L{{ nestingDepth }}</span>
              </el-tag>
              <!-- SAFE 级自动通过徽章（Phase F1） -->
              <el-tag v-if="isAutoApproved" size="small" type="success" effect="plain">自动通过</el-tag>
              <!-- 高危风险等级徽章（Phase G2） -->
              <el-tag v-if="isHighRisk" size="small" type="danger" effect="dark">高危</el-tag>
              <el-tag v-if="isSkillCall && skillModeLabel" size="small" :type="skillModeLabel === '管线' ? 'primary' : skillModeLabel === '顾问' ? 'success' : 'warning'" effect="plain">{{ skillModeLabel }}</el-tag>
              <el-tag v-else-if="status === ToolCallStatus.REJECTED" size="small" type="danger">已拒绝</el-tag>
              <el-tag v-else :type="statusType" size="small">{{ statusText }}</el-tag>
            </div>
            <div v-if="!isExpanded && summaryLine" class="tool-summary">{{ summaryLine }}</div>
          </div>
        </div>
        <el-icon class="expand-icon" :class="{ 'rotated': isExpanded }">
          <ArrowDown />
        </el-icon>
      </div>

      <div v-if="description" class="tool-call-description">{{ description }}</div>

      <div v-if="isExpanded" class="tool-call-content">
        <!-- 输入区：按 inputDisplay.displayMode 差异化渲染 -->
        <div v-if="inputDisplay.displayMode !== 'skip'" class="tool-call-section">
          <div class="section-title">输入参数</div>
          <pre :class="inputContentClass">{{ inputDisplay.formatted }}</pre>
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
            <div class="section-content">{{ output }}</div>
          </div>
        </template>
        <!-- 普通输出：仅终态展示（防止审批阶段提前渲染 result 字段） -->
        <template v-else>
          <div v-if="shouldShowOutput" class="tool-call-section">
            <div class="section-title">输出结果</div>
            <pre v-if="outputDisplay.displayMode !== 'skip'" :class="outputContentClass">{{ outputDisplay.formatted }}</pre>
            <!-- 拒绝/超时且无结果数据时给出占位反馈，保证输出结果区域不消失 -->
            <pre v-else-if="status === ToolCallStatus.REJECTED || status === ToolCallStatus.TIMEOUT" class="section-content section-content--rejected-feedback">
{{ status === ToolCallStatus.REJECTED ? '已拒绝执行该工具，Agent 将调整策略继续' : '该工具审批超时，未执行' }}
            </pre>
          </div>
        </template>

        <!-- 审批面板 -->
        <div v-if="isWaiting || isWaitingForSiblings" class="approval-panel" :class="{ 'approval-panel--high-risk': isHighRisk }">
          <div class="approval-panel__header">
            <span class="approval-panel__title">审批确认</span>
            <el-tag size="small" :type="riskLevelTagType" effect="dark">{{ riskLevelLabel }}</el-tag>
          </div>
          <div v-if="isWaitingForSiblings" class="approval-panel__waiting-hint">
            <el-icon><Clock /></el-icon>
            <span>{{ isWaitingForSiblingsRejected ? '本工具已拒绝，等待其余工具完成审批' : '本工具已审批，等待其余工具完成审批' }}</span>
          </div>
          <div v-if="approvalData.title" class="approval-panel__title-text">{{ approvalData.title }}</div>
          <div v-if="approvalData.description" class="approval-panel__desc">{{ approvalData.description }}</div>
          <div v-if="approvalArgs.length > 0 && !isWaitingForSiblings && !isProcessingApproval" class="approval-panel__command">
            <div v-for="(arg, idx) in approvalArgs" :key="idx" class="approval-panel__arg">
              <span class="approval-panel__command-label">{{ arg.key }}</span>
              <pre class="approval-panel__code">{{ arg.value }}</pre>
            </div>
          </div>
          <div v-if="isConfirmWithInput && !isWaitingForSiblings && !isProcessingApproval" class="approval-panel__input">
            <el-input
              v-model="approvalInputValue"
              :placeholder="approvalData.inputPlaceholder || '请输入值...'"
              size="small"
              clearable
              @keyup.enter="emit('approve', { ...toolCall, _userInput: approvalInputValue })"
            />
          </div>
          <div class="approval-panel__actions">
            <el-button type="danger" size="small" :disabled="isWaitingForSiblings || isProcessingApproval || approvalDisabled" @click.stop="emit('reject', toolCall)">拒绝</el-button>
            <el-button
              type="primary"
              size="small"
              :disabled="isWaitingForSiblings || isProcessingApproval || approvalDisabled"
              @click.stop="emit('approve', isConfirmWithInput ? { ...toolCall, _userInput: approvalInputValue } : toolCall)"
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
  transition: border-color 0.25s ease, border-left-color 0.25s ease, border-left-width 0.25s ease;
}

.tool-call-card--failed {
  border-left: 3px solid var(--el-color-danger);
}

.tool-call-card--running {
  border-left: 3px solid var(--el-color-warning);
}

.tool-call-card--waiting {
  border-left: 3px solid var(--el-color-info);
}

.tool-call-card--rejected {
  border-left: 3px solid var(--el-color-danger);
}

.tool-call-card--timeout {
  border-left: 3px solid var(--el-color-danger);
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
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  min-width: 0;
}

.tool-info__row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  min-width: 0;
}

.tool-summary {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
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

/* displayMode 差异化渲染样式（由 toolAdapters.js formatToolParameters/formatToolResult 返回值驱动） */
/* inline: 单行展示（如 read_file 的 file_path、grep 的 pattern），无背景框 */
.section-content--inline {
  display: inline-block;
  padding: 4px 8px;
  background-color: var(--el-fill-color-light);
  font-family: 'Courier New', monospace;
  font-size: 12px;
  max-height: none;
  white-space: pre-wrap;
}

/* monospace / command: 等宽字体展示（如 execute 的 shell 输出），保留 monospace 背景 */
.section-content--monospace {
  font-family: 'Courier New', 'Consolas', monospace;
  background-color: var(--el-fill-color-darker);
  color: var(--el-text-color-primary);
}

/* command: shell 命令展示（input 区，与 monospace 区分：保留 default 背景，仅设字体） */
.display-mode--command {
  font-family: 'Courier New', 'Consolas', monospace;
  font-weight: 500;
}

/* diff: 差异展示（edit_file 的 old_string/new_string），保留 pre 默认样式 */
.display-mode--diff {
  border-left: 3px solid var(--el-color-warning);
}

/* list: 列表展示（如 glob 的匹配文件列表），保留 pre 默认样式 */
.display-mode--list {
  white-space: pre;
}

/* code: 代码展示（如 read_file 结果），保留 pre 默认样式（language 字段供后续接入 highlight.js） */
.display-mode--code {
  font-family: 'Courier New', 'Consolas', monospace;
}

.status-icon.status-waiting {
  color: var(--el-color-info);
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

/* P19 修复：按钮 disabled 样式（等待同批审批） */
.approval-panel__actions .el-button.is-disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.approval-panel__waiting-hint {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  margin-bottom: 10px;
  background: var(--el-color-info-light-9);
  border: 1px solid var(--el-color-info-light-5);
  border-radius: 4px;
  font-size: 13px;
  color: var(--el-color-info);
}

.approval-panel__waiting-hint .el-icon {
  font-size: 16px;
  flex-shrink: 0;
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

/* ===== Phase G2: HIGH 级高危红名高亮 ===== */
.tool-call-card--high-risk {
  border-color: var(--el-color-danger) !important;
  border-width: 2px;
  background-color: var(--el-color-danger-light-9);
}

.tool-name--high-risk {
  color: var(--el-color-danger);
  font-weight: 700;
}

.approval-panel--high-risk {
  border-color: var(--el-color-danger) !important;
  border-width: 2px;
  background-color: var(--el-color-danger-light-9);
}

/* ===== Phase G3: 子 agent 嵌套链路展示 ===== */
.agent-path-tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
}

.agent-path-icon {
  font-size: 12px;
  margin-right: 2px;
}

.depth-badge {
  display: inline-block;
  padding: 0 4px;
  background-color: var(--el-color-info-light-7);
  border-radius: 3px;
  font-size: 10px;
  font-weight: 600;
  margin-left: 2px;
}

.tool-call-card--subagent {
  border-left-style: dashed;
}

/* ===== Task 4.4: 触发嵌套子代理的工具（异步子代理调用）独立视觉 ===== */
.tool-call-card--subagent-trigger {
  border-left: 3px solid var(--el-color-primary);
  border-left-style: dashed;
  background-color: var(--el-color-primary-light-9);
}

.subagent-trigger-tag {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  font-size: 11px;
}

.subagent-trigger-icon {
  font-size: 12px;
}
</style>
