<script setup>
/**
 * SubAgentDetailPanel —— 子代理卡片体（SubAgentCard 展开后的内容区，支持嵌套递归）
 *
 * 结构：思考（AiReasoning）→ 正文 + 工具 position 内联（InlineToolCallContent
 * 工具插槽渲染 ToolCallCard）。头部（标题/状态）由 SubAgentCard 承担，本组件
 * 只渲染内容，视觉上整体位于子代理卡片组件内部。
 *
 * 嵌套递归：本面板 toolCalls 中的 spawn_sub_agent 工具之后挂载其直接子代理
 * SubAgentCard（spawnToolCallId ↔ toolCall.id 精确关联），展开时递归渲染嵌套
 * 卡片，深层由递归卡片自行处理。每个卡片只挂自己的直接子层。
 *
 * 状态/事件统一由宿主管理并向上冒泡：
 * - expandedThreadIds（Set）+ toggle(threadId)：展开状态；
 * - approve/reject(toolCall)：审批事件（嵌套层递归透传）。
 * 孤儿（无 spawnToolCallId）由宿主在主工具流末尾渲染，本组件不处理。
 *
 * 审批控件：内嵌于 ToolCallCard 审批面板，与主 agent 审批同一组件/同一链路。
 */
import { computed } from 'vue'
import AiReasoning from '../ai-elements/AiReasoning.vue'
import InlineToolCallContent from '../common/InlineToolCallContent.vue'
import ToolCallCard from './ToolCallCard.vue'
import SubAgentCard from './SubAgentCard.vue'
import { deriveDisplayStatus } from '../../utils/toolCallStateMachine'
import { mapSubagentsBySpawnToolCall } from '../../utils/subagentAggregation'

const props = defineProps({
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
  /** 推理区展示文案（父组件按语境传入："研究推理" / "思考过程"） */
  reasoningLabel: {
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
  /** 全量子代理视图数组（宿主传入；本面板内部按 spawnToolCallId 过滤直接子层） */
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

// 本面板工具调用 ID 集合（toolCall.id 与后端 tool_call_id 同值）
const ownToolCallIds = computed(() => {
  const ids = new Set()
  for (const tc of props.toolCalls) {
    const id = tc?.id || tc?.toolCallId
    if (id) ids.add(id)
  }
  return ids
})

// 直接子层索引：spawnToolCallId 指向本面板工具调用的子代理（深层由递归卡片处理）
const directChildBySpawnToolCallId = computed(() => {
  const { bySpawnToolCallId } = mapSubagentsBySpawnToolCall(props.subagents)
  const direct = new Map()
  for (const [spawnToolCallId, subagent] of bySpawnToolCallId) {
    if (ownToolCallIds.value.has(spawnToolCallId)) {
      direct.set(spawnToolCallId, subagent)
    }
  }
  return direct
})

/** 取 spawn 工具调用对应的直接子代理（无则空数组，模板 v-for 局部变量化） */
const directChildrenOf = (toolCall) => {
  const id = toolCall?.id || toolCall?.toolCallId
  if (!id) return []
  const child = directChildBySpawnToolCallId.value.get(id)
  return child ? [child] : []
}

const isThreadExpanded = (threadId) => props.expandedThreadIds?.has(threadId) === true
</script>

<template>
  <div class="subagent-detail">
    <!-- 思考 -->
    <AiReasoning
      v-if="reasoningContent"
      :content="reasoningContent"
      :is-streaming="false"
      source="deep_thinking"
      :reasoning-label="reasoningLabel"
    />

    <!-- 正文 + 工具内联切段（工具插槽渲染 ToolCallCard + 嵌套子代理） -->
    <InlineToolCallContent
      :content="content"
      :tool-calls="toolCalls"
      :approval-disabled="approvalDisabled"
    >
      <template #tool="{ toolCall }">
        <ToolCallCard
          :tool-name="toolCall.name"
          :input="toolCall.input || toolCall.parameters"
          :output="toolCall.output || toolCall.result"
          :status="deriveDisplayStatus(toolCall)"
          :tool-call="toolCall"
          :approval-disabled="approvalDisabled"
          :is-subagent-trigger="toolCall.name === 'spawn_sub_agent'"
          @approve="(tc) => emit('approve', tc)"
          @reject="(tc) => emit('reject', tc)"
        />

        <!-- 嵌套递归：spawn 工具之后挂载直接子代理卡片（含自身展开内容，递归）。
             嵌套卡片审批自治：按各自 subagent.status 判断，不透传本层 approvalDisabled -->
        <template v-if="toolCall.name === 'spawn_sub_agent'">
          <div v-for="child in directChildrenOf(toolCall)" :key="child.threadId" class="subagent-detail__nested">
            <SubAgentCard
              :subagent="child"
              :expanded="isThreadExpanded(child.threadId)"
              :reasoning-label="reasoningLabel"
              :subagents="subagents"
              :expanded-thread-ids="expandedThreadIds"
              @toggle="(threadId) => emit('toggle', threadId)"
              @approve="(tc) => emit('approve', tc)"
              @reject="(tc) => emit('reject', tc)"
            />
          </div>
        </template>
      </template>
    </InlineToolCallContent>
  </div>
</template>

<style scoped>
/* 卡片体：位于 SubAgentCard 卡片内部，仅用顶部边框分隔头部与内容区 */
.subagent-detail {
  width: 100%;
  padding: 8px 12px 12px;
  border-top: 1px solid var(--el-border-color-lighter);
}

/* 嵌套子代理卡片缩进（层级视觉区分，沿用现有边框/背景变量） */
.subagent-detail__nested {
  margin-left: 12px;
  margin-top: 8px;
}
</style>
