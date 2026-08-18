<script setup>
/**
 * SubAgentDetailPanel —— 子代理卡片体（SubAgentCard 展开后的内容区，支持嵌套递归）
 *
 * 统一渲染管线（spec unify-agent-research-display-architecture）：思考 → 正文 +
 * 工具 position 内联（ToolCallCard）+ spawn 后嵌套子代理卡片递归渲染。头部
 * （标题/状态）由 SubAgentCard 承担，本组件只渲染内容，视觉上整体位于子代理
 * 卡片组件内部。
 *
 * 嵌套递归：管线内 spawn_sub_agent 工具之后挂载其派生子代理 SubAgentCard
 * （spawnToolCallId ↔ toolCall.id 精确关联），展开时递归渲染嵌套卡片，深层由
 * 递归卡片自行处理。每个卡片只挂自己的直接子层。
 *
 * 状态/事件统一由宿主管理并向上冒泡：
 * - expandedThreadIds（Set）+ toggle(threadId)：展开状态；
 * - approve/reject(toolCall)：审批事件（嵌套层递归透传）。
 * 孤儿（无 spawnToolCallId）由宿主在主工具流末尾渲染，本组件不处理。
 *
 * 审批控件：内嵌于 ToolCallCard 审批面板，与主 agent 审批同一组件/同一链路。
 */
import AgentContentPipeline from '../common/AgentContentPipeline.vue'

defineProps({
  /** 子代理正文（父组件按 subagentThreadId 聚合后传入） */
  content: {
    type: String,
    default: '',
  },
  /** 子代理中间思考 */
  reasoningContent: {
    type: String,
    default: '',
  },
  /** 子代理工具调用列表（父组件按 subagentThreadId 聚合后传入） */
  toolCalls: {
    type: Array,
    default: () => [],
  },
  /** 本面板所属子代理非「等待你的确认」时置灰本层工具卡审批控件
   *  （由 SubAgentCard 按该子代理自身状态计算；嵌套子代理卡片自治，不再透传） */
  approvalDisabled: {
    type: Boolean,
    default: false,
  },
  /** 全量子代理视图数组（宿主传入；管线内部按 spawnToolCallId 过滤直接子层） */
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

defineOptions({ name: 'SubAgentDetailPanel' })

const emit = defineEmits(['toggle', 'approve', 'reject'])
</script>

<template>
  <div class="subagent-detail">
    <!-- 统一渲染管线：思考 → 正文 + 工具内联 → spawn 后嵌套子代理递归
         （spec unify-agent-research-display-architecture） -->
    <AgentContentPipeline
      :content="content"
      :reasoning-content="reasoningContent"
      :tool-calls="toolCalls"
      :subagents="subagents"
      :expanded-thread-ids="expandedThreadIds"
      :approval-disabled="approvalDisabled"
      @toggle-subagent="(threadId) => emit('toggle', threadId)"
      @approve="(tc) => emit('approve', tc)"
      @reject="(tc) => emit('reject', tc)"
    />
  </div>
</template>

<style scoped>
/* 卡片体：位于 SubAgentCard 卡片内部，仅用顶部边框分隔头部与内容区 */
.subagent-detail {
  width: 100%;
  padding: 8px 12px 12px;
  border-top: 1px solid var(--el-border-color-lighter);
}

/* 嵌套子代理卡片缩进（层级视觉区分，沿用现有边框/背景变量）。
   原 .subagent-detail__nested（12px 左缩进 + 8px 上间距）收敛为对管线内
   SubAgentCard 的深度选择器缩进：递归嵌套时逐层叠加，与原实现一致 */
.subagent-detail :deep(.agent-content-pipeline .subagent-card) {
  margin-left: 12px;
  margin-top: 8px;
}
</style>
