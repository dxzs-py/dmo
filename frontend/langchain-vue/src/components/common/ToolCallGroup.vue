<script setup>
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { ArrowDown, Connection, CircleCheck, Close, Loading, RefreshRight, Warning } from '@element-plus/icons-vue'
import ToolCallCard from '@/components/chat/ToolCallCard.vue'
import {
  buildToolTree,
  computeGroupStats,
  findChildGroupsByTool,
  formatDuration,
  getToolCallId,
} from '@/utils/toolCallTree'
import { deepResearchAPI } from '@/api/research'

/**
 * 子代理分组卡片（公共组件：深度研究模块与聊天模块共用，Task 6：递归嵌套式容器树，参考 3.md）
 *
 * 宏观任务单元：区分不同子代理，聚合整体进度/统计/总耗时，支持折叠。
 * 内部渲染组内工具调用树（按 parentToolCallId 递归缩进），
 * 工具触发新子 Agent 时在该工具节点内嵌子代理分组卡片（递归）。
 *
 * 自动折叠规则：
 * - 运行中（running/waiting/pending）→ 默认展开，实时展示执行过程
 * - 全部完成（completed）→ 默认折叠，减少冗余信息
 * - 失败/超时/拒绝（failed）→ 强制展开 + 高亮
 *
 * 组头聚合区（5.md 特性2 补全）：
 * - 名称/面包屑/统计/状态/折叠（既有）
 * - 总耗时（Task 4.2）：组终态时显示 min(createdAt) → max(completedAt) 之差
 * - 任务目标描述（Task 4.5）：子代理静态角色描述（截断 + hover 完整）
 * - hover 浮层（Task 4.3）：完整 agentPath 面包屑 + 工具统计
 * - 重试按钮（Task 6）：组状态 failed/timeout 且存在触发工具时，调用
 *   POST /research/task/<taskId>/retry-subagent/ 单独重启失败子代理
 *
 * @param {Object} props.group - buildAgentGroups 分组对象
 * @param {Array} props.groups - 全部分组（嵌套查找子组）
 * @param {number} props.depth - 嵌套层级（视觉缩进），默认 0
 * @param {string} props.taskId - 深度研究任务 ID（重试 API 入参，可选）
 */
const props = defineProps({
  group: {
    type: Object,
    required: true,
  },
  groups: {
    type: Array,
    default: () => [],
  },
  depth: {
    type: Number,
    default: 0,
  },
  taskId: {
    type: String,
    default: '',
  },
})

const emit = defineEmits(['approve', 'reject'])

// 组内工具树（按 parentToolCallId 递归）
const tree = computed(() => buildToolTree(props.group.toolCalls || []))

// 整体状态与统计
const status = computed(() => props.group.status || 'pending')
const stats = computed(() => props.group.stats || computeGroupStats(props.group.toolCalls || []))
const isFailed = computed(() => status.value === 'failed' || status.value === 'timeout')
const isRunning = computed(() => status.value === 'running' || status.value === 'waiting' || status.value === 'pending')

// 自动折叠规则：完成折叠；运行中/失败展开
const isCollapsed = ref(status.value === 'completed')
// 组状态变化时同步折叠（运行中 → 展开，失败 → 强制展开）
if (isRunning.value || isFailed.value) {
  isCollapsed.value = false
}

// 组头面包屑（如 main → web-researcher）
const breadcrumbText = computed(() => (props.group.agentPath || []).join(' → '))

// 组终态集合（仅终态组展示总耗时；运行中组状态标签已表达"进行中"）
const TERMINAL_STATUSES = new Set(['completed', 'failed', 'rejected', 'timeout'])

// 组头总耗时（Task 4.2）：组终态且 stats.durationMs 可计算时展示（如 "耗时 12.3s" / "耗时 <1s"）
const durationText = computed(() => {
  if (!TERMINAL_STATUSES.has(status.value)) return null
  const ms = stats.value.durationMs
  if (typeof ms !== 'number') return null
  const d = formatDuration(ms)
  return d ? `耗时 ${d}` : null
})

// 子代理任务目标描述（Task 4.5）：buildAgentGroups 组级共享字段
const description = computed(() => props.group.description || '')

// 触发该子代理组的父工具调用 ID（Task 6 重试入参 tool_call_id）：
// 组内第一条 parentToolCallId 指向组外工具的工具，其 parentToolCallId 即触发工具；
// 根组（main）无触发工具，返回空串（不提供重试入口）
const triggerToolCallId = computed(() => {
  const toolCalls = props.group.toolCalls || []
  const ids = new Set(toolCalls.map((tc) => getToolCallId(tc)))
  const entry = toolCalls.find((tc) => tc.parentToolCallId && !ids.has(tc.parentToolCallId))
  return entry ? entry.parentToolCallId : ''
})

// 重试失败子代理（Task 6）：组状态 failed/timeout 且存在触发工具时展示按钮
const isRetrying = ref(false)
const canRetry = computed(() => isFailed.value && !!props.taskId && !!triggerToolCallId.value)

const handleRetry = async () => {
  if (!props.taskId || !triggerToolCallId.value) return
  isRetrying.value = true
  try {
    // 入参为前端 camelCase 键，axios 请求拦截器 toSnakeCase 转为后端契约键
    // agent_path / tool_call_id（网络协议边界）
    await deepResearchAPI.retrySubagent(props.taskId, {
      agentPath: props.group.agentPath || [],
      toolCallId: triggerToolCallId.value,
    })
    ElMessage.success('重试已提交')
  } catch (err) {
    const detail = err?.response?.data?.message
      || err?.response?.data?.detail
      || err?.message
      || '重试提交失败'
    ElMessage.error(detail)
  } finally {
    isRetrying.value = false
  }
}

const statusMeta = computed(() => {
  const map = {
    running: { type: 'warning', text: '执行中', icon: Loading },
    waiting: { type: 'info', text: '等待中', icon: ArrowDown },
    pending: { type: 'info', text: '待执行', icon: ArrowDown },
    completed: { type: 'success', text: '已完成', icon: CircleCheck },
    failed: { type: 'danger', text: '失败', icon: Close },
    timeout: { type: 'danger', text: '已超时', icon: Warning },
    rejected: { type: 'danger', text: '已拒绝', icon: Close },
  }
  return map[status.value] || { type: 'info', text: status.value, icon: ArrowDown }
})

/** 单条工具 → ToolCallCard props 映射 */
const toCardProps = (tc) => ({
  toolName: tc.name || tc.toolName || 'unknown',
  description: tc.description || '',
  input: tc.input || tc.parameters || null,
  output: tc.output || tc.result || null,
  status: tc.status || 'pending',
  toolCall: tc,
})
</script>

<template>
  <div
    :class="[
      'tool-call-group',
      `tool-call-group--${status}`,
      { 'tool-call-group--nested': depth > 0, 'tool-call-group--failed': isFailed },
    ]"
  >
    <!-- 组头：聚合信息（hover 浮层展示完整 agentPath + 工具统计，Task 4.3） -->
    <el-tooltip
      placement="top"
      :show-after="400"
      :teleported="true"
      popper-class="group-header-hover-popper"
      class="group-header-tooltip-wrapper"
    >
      <template #content>
        <div class="group-header-hover">
          <div class="group-header-hover__path">
            <el-icon><Connection /></el-icon>
            <span>{{ breadcrumbText || 'main' }}</span>
          </div>
          <div class="group-header-hover__stats">
            共 {{ stats.total }} 个工具
            <template v-if="stats.completed > 0"> · 完成 {{ stats.completed }}</template>
            <template v-if="stats.running > 0"> · 执行中 {{ stats.running }}</template>
            <template v-if="stats.failed > 0"> · 失败 {{ stats.failed }}</template>
            <template v-if="durationText"> · {{ durationText }}</template>
          </div>
        </div>
      </template>
      <div class="group-header" @click="isCollapsed = !isCollapsed">
        <div class="group-header__left">
          <el-icon class="group-header__icon" :class="`group-icon--${status}`">
            <component :is="statusMeta.icon" :class="{ 'is-loading': status === 'running' }" />
          </el-icon>
          <div class="group-header__info">
            <div class="group-header__title">
              <span class="group-agent-name">{{ group.agentName || 'Agent' }}</span>
              <!-- 子代理任务目标描述（Task 4.5）：截断展示 + hover 完整 -->
              <el-tooltip v-if="description" :content="description" placement="top" :show-after="300">
                <span class="group-description">{{ description }}</span>
              </el-tooltip>
              <el-tag size="small" :type="statusMeta.type" effect="light">{{ statusMeta.text }}</el-tag>
              <el-tag v-if="isFailed" size="small" type="danger" effect="dark">需关注</el-tag>
            </div>
            <div v-if="breadcrumbText && group.agentPath && group.agentPath.length > 1" class="group-header__breadcrumb">
              <el-icon class="breadcrumb-icon"><Connection /></el-icon>
              <span>{{ breadcrumbText }}</span>
            </div>
          </div>
        </div>
        <div class="group-header__right">
          <div class="group-stats">
            <span class="stat stat--total">共 {{ stats.total }}</span>
            <span v-if="durationText" class="stat stat--duration">{{ durationText }}</span>
            <span v-if="stats.completed > 0" class="stat stat--completed">完成 {{ stats.completed }}</span>
            <span v-if="stats.running > 0" class="stat stat--running">执行中 {{ stats.running }}</span>
            <span v-if="stats.failed > 0" class="stat stat--failed">失败 {{ stats.failed }}</span>
          </div>
          <!-- 重试失败子代理（Task 6） -->
          <el-button
            v-if="canRetry"
            size="small"
            type="danger"
            plain
            :loading="isRetrying"
            class="group-retry-btn"
            @click.stop="handleRetry"
          >
            <el-icon v-if="!isRetrying" class="group-retry-icon"><RefreshRight /></el-icon>
            重试
          </el-button>
          <el-icon class="group-header__expand" :class="{ 'rotated': !isCollapsed }">
            <ArrowDown />
          </el-icon>
        </div>
      </div>
    </el-tooltip>

    <!-- 组体：工具调用树（递归） -->
    <div v-if="!isCollapsed" class="group-body">
      <template v-for="node in tree" :key="node.id || node.toolCallId || `${node.name}-${node.status}`">
        <div class="tool-tree-node">
          <ToolCallCard
            v-bind="toCardProps(node)"
            :is-subagent-trigger="findChildGroupsByTool(groups, node.id || node.toolCallId).length > 0"
            @approve="(tc) => emit('approve', tc)"
            @reject="(tc) => emit('reject', tc)"
          />

          <!-- 子工具（parentToolCallId 递归缩进） -->
          <div v-if="node.children && node.children.length > 0" class="tool-tree-children">
            <div
              v-for="child in node.children"
              :key="child.id || child.toolCallId || `${child.name}-${child.status}`"
              class="tool-tree-node"
            >
              <ToolCallCard
                v-bind="toCardProps(child)"
                :is-subagent-trigger="findChildGroupsByTool(groups, child.id || child.toolCallId).length > 0"
                @approve="(tc) => emit('approve', tc)"
                @reject="(tc) => emit('reject', tc)"
              />
              <!-- 子工具触发的子代理分组（内嵌递归卡片） -->
              <div
                v-if="findChildGroupsByTool(groups, child.id || child.toolCallId).length > 0"
                class="tool-tree-subgroups"
              >
                <ToolCallGroup
                  v-for="childGroup in findChildGroupsByTool(groups, child.id || child.toolCallId)"
                  :key="childGroup.key"
                  :group="childGroup"
                  :groups="groups"
                  :depth="depth + 1"
                  :task-id="taskId"
                  @approve="(tc) => emit('approve', tc)"
                  @reject="(tc) => emit('reject', tc)"
                />
              </div>
            </div>
          </div>

          <!-- 该工具触发的子代理分组（内嵌递归卡片） -->
          <div v-if="findChildGroupsByTool(groups, node.id || node.toolCallId).length > 0" class="tool-tree-subgroups">
            <ToolCallGroup
              v-for="childGroup in findChildGroupsByTool(groups, node.id || node.toolCallId)"
              :key="childGroup.key"
              :group="childGroup"
              :groups="groups"
              :depth="depth + 1"
              :task-id="taskId"
              @approve="(tc) => emit('approve', tc)"
              @reject="(tc) => emit('reject', tc)"
            />
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.tool-call-group {
  border: 1px solid var(--el-border-color);
  border-radius: 10px;
  margin: 10px 0;
  background-color: var(--el-bg-color);
  overflow: hidden;
  transition: border-color 0.25s ease;
}

.tool-call-group--nested {
  margin: 8px 0 8px 8px;
  border-left: 3px solid var(--el-border-color);
}

.tool-call-group--failed {
  border-color: var(--el-color-danger-light-5);
  border-left: 3px solid var(--el-color-danger);
  background-color: var(--el-color-danger-light-9);
}

.group-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  cursor: pointer;
  background-color: var(--el-fill-color-light);
  transition: background-color 0.2s;
}

.group-header:hover {
  background-color: var(--el-fill-color);
}

.group-header__left {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.group-header__icon {
  font-size: 20px;
  flex-shrink: 0;
}

.group-icon--running {
  color: var(--el-color-warning);
}

.group-icon--completed {
  color: var(--el-color-success);
}

.group-icon--failed {
  color: var(--el-color-danger);
}

.group-icon--running .is-loading {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.group-header__info {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.group-header__title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.group-agent-name {
  font-weight: 600;
  color: var(--el-text-color-primary);
  white-space: nowrap;
}

/* 子代理任务目标描述（Task 4.5）：截断 + hover 完整 */
.group-description {
  max-width: 260px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.group-header__breadcrumb {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.breadcrumb-icon {
  font-size: 12px;
}

.group-header__right {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}

.group-stats {
  display: flex;
  gap: 8px;
  font-size: 12px;
}

.stat {
  padding: 2px 8px;
  border-radius: 4px;
  background-color: var(--el-bg-color-page);
}

.stat--completed { color: var(--el-color-success); }
.stat--running { color: var(--el-color-warning); }
.stat--failed { color: var(--el-color-danger); }
.stat--duration { color: var(--el-text-color-secondary); }

.group-retry-btn {
  margin-left: 4px;
}

.group-retry-icon {
  margin-right: 2px;
}

.group-header__expand {
  color: var(--el-text-color-secondary);
  transition: transform 0.2s;
}

.group-header__expand.rotated {
  transform: rotate(180deg);
}

.group-body {
  padding: 4px 16px 12px;
}

.tool-tree-children {
  margin-left: 20px;
  padding-left: 12px;
  border-left: 2px dashed var(--el-border-color-lighter);
}

.tool-tree-subgroups {
  margin-top: 4px;
}
</style>

<style>
/* ===== ToolCallGroup 组头 hover 浮层（Task 4.3）=====
 * popper 经 teleport 渲染到 body 下，scoped 样式不生效，使用非 scoped 块。
 * 展示完整 agentPath 面包屑与工具调用统计（组头截断内容的完整值）。
 */
.group-header-tooltip-wrapper {
  display: block;
  width: 100%;
}

.group-header-hover-popper {
  max-width: 420px;
}

.group-header-hover {
  font-size: 12px;
  line-height: 1.6;
  color: var(--el-text-color-primary);
}

.group-header-hover__path {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  word-break: break-all;
}

.group-header-hover__stats {
  margin-top: 6px;
  color: var(--el-text-color-secondary);
}
</style>
