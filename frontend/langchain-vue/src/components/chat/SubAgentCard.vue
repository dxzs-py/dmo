<script setup>
/**
 * SubAgentCard —— 子代理总卡片（头部 + 展开内容一体）
 *
 * 头部：标题「子代理 N · Ld」+ 中文状态 + 结果预览（完成时，3 行截断）。
 * 点击卡片原地展开，卡片体内渲染 SubAgentDetailPanel（思考 → 正文 + 工具
 * position 内联 + 嵌套子代理），与主消息视觉明确区分。
 *
 * 事件：toggle(threadId) 展开/收起；approve/reject(toolCall) 审批事件
 * （面板内 ToolCallCard 冒泡，经本卡片转发到宿主）。
 */
import { computed } from 'vue'
import { ArrowRight } from '@element-plus/icons-vue'
import SubAgentDetailPanel from './SubAgentDetailPanel.vue'
import {
  getSubagentStatusTagType,
  getSubagentStatusText,
  isPendingUserInput,
} from '../../utils/subagentStatus'
import { formatSubagentTitle } from '../../utils/subagentAggregation'

const props = defineProps({
  /** 子代理实例数据（SubAgentInstance 前端 camelCase 视图，含 content/reasoningContent/toolCalls） */
  subagent: {
    type: Object,
    required: true,
  },
  /** 是否已展开（由父组件控制） */
  expanded: {
    type: Boolean,
    default: false,
  },
  /** 全量子代理视图数组（嵌套层挂载数据源，经展开面板递归使用） */
  subagents: {
    type: Array,
    default: () => [],
  },
  /** 已展开的子代理 threadId 集合（宿主统一管理，嵌套层透传） */
  expandedThreadIds: {
    type: Set,
    default: () => new Set(),
  },
})

defineOptions({ name: 'SubAgentCard' })

const emit = defineEmits(['toggle', 'approve', 'reject'])

// 标题：明确标识「子代理 + 序号 + 层级」（主代理=L0，直接子代理=L1，嵌套 L2+），
// 后接 task 摘要（中文任务描述优先，回退 agentName，再回退「子代理」）
const title = computed(() => {
  const order = props.subagent.order
  const depth = props.subagent.depth
  const prefix = order ? `子代理 ${order}${depth ? ` · L${depth}` : ''}：` : ''
  return `${prefix}${formatSubagentTitle(props.subagent.task, props.subagent.agentName)}`
})
const statusText = computed(() => getSubagentStatusText(props.subagent.status))
const statusTagType = computed(() => getSubagentStatusTagType(props.subagent.status))
const resultPreview = computed(() => props.subagent.resultPreview || '')
const needsConfirm = computed(() => isPendingUserInput(props.subagent.status))

/** 待审批项数量（pending_interrupt_info.requests 批量结构，spec D12） */
const pendingCount = computed(() => {
  const info = props.subagent.pendingInterruptInfo
  const requests = info && typeof info === 'object' ? info.requests : null
  return Array.isArray(requests) ? requests.length : 0
})

// spec D8.4：等待你的确认时摘要卡片提示「等待你的确认，共 N 项待审批」
const hint = computed(() => {
  if (needsConfirm.value) {
    return pendingCount.value > 0 ? `共 ${pendingCount.value} 项待审批` : '点击卡片进入处理确认操作'
  }
  if (props.expanded) return '点击收起'
  return '点击展开详情'
})

const handleClick = () => emit('toggle', props.subagent.threadId)

// 审批自治（对齐 7.md：子代理审批只和子代理自身有关）：
// 本子代理处于「等待你的确认」时审批控件可交互，否则置灰。
// 不依赖父任务状态 / 父消息固化状态（嵌套层卡片各自按自身状态判断）。
const ownApprovalDisabled = computed(() => !isPendingUserInput(props.subagent.status))
</script>

<template>
  <div
    class="subagent-card"
    :class="{ 'subagent-card--confirm': needsConfirm, 'subagent-card--expanded': expanded }"
  >
    <div class="subagent-card__header" @click="handleClick">
      <el-icon class="subagent-card__arrow" :class="{ 'is-expanded': expanded }">
        <ArrowRight />
      </el-icon>
      <span class="subagent-card__name">{{ title }}</span>
      <el-tag size="small" :type="statusTagType" effect="plain">{{ statusText }}</el-tag>
      <span class="subagent-card__hint">{{ hint }}</span>
    </div>

    <div v-if="resultPreview && !expanded" class="subagent-card__preview">{{ resultPreview }}</div>

    <!-- 展开内容在卡片体内（思考 → 正文 + 工具内联 + 嵌套子代理），
         与主消息视觉区分：整体为一个子代理卡片组件 -->
    <SubAgentDetailPanel
      v-if="expanded"
      :content="subagent.content"
      :reasoning-content="subagent.reasoningContent"
      :tool-calls="subagent.toolCalls"
      :approval-disabled="ownApprovalDisabled"
      :subagents="subagents"
      :expanded-thread-ids="expandedThreadIds"
      @toggle="(threadId) => emit('toggle', threadId)"
      @approve="(tc) => emit('approve', tc)"
      @reject="(tc) => emit('reject', tc)"
    />
  </div>
</template>

<style scoped>
.subagent-card {
  width: 100%;
  border: 1px solid var(--el-border-color-light);
  border-radius: 10px;
  background-color: var(--el-bg-color-page);
  margin: 8px 0;
  cursor: pointer;
  overflow: hidden;
  transition: border-color 0.2s, box-shadow 0.2s;
}

.subagent-card:hover {
  border-color: var(--el-color-primary-light-5);
}

.subagent-card--confirm {
  border-color: var(--el-color-warning-light-5);
}

.subagent-card--expanded {
  box-shadow: var(--el-box-shadow-light);
}

.subagent-card__header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
}

.subagent-card__arrow {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  transition: transform 0.2s;
}

.subagent-card__arrow.is-expanded {
  transform: rotate(90deg);
}

.subagent-card__name {
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.subagent-card__status-icon {
  font-size: 14px;
  line-height: 1;
}

.subagent-card__hint {
  margin-left: auto;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.subagent-card__preview {
  padding: 0 12px 8px 28px;
  font-size: 13px;
  color: var(--el-text-color-regular);
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  /* 摘要截断：预览最多 3 行 + 省略号（完整内容点击展开面板查看） */
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
