<script setup>
/**
 * AgentContentPipeline —— 智能体/深度研究展示层统一渲染管线
 *
 * 一次实现三段落结构（spec unify-agent-research-display-architecture）：
 *   AI 推理区（AiReasoning）→ 正文内联工具切段（InlineToolCallContent 的
 *   #tool 插槽渲染 ToolCallCard，spawn 工具之后紧跟其派生的 SubAgentCard）
 *   → orphans（无 spawnToolCallId 的 SubAgentCard）兜底渲染。
 *
 * ChatMessage / ResearchTaskDetail / SubAgentDetailPanel 三处替换调用，
 * 各调用方仅传入差异化数据与事件回调；ToolCallCard 五元组绑定
 * （tool-name / input / output / status / approval-disabled）在本组件内一次实现。
 *
 * 事件：toggle-subagent(threadId) 展开/收起；approve/reject(toolCall) 审批
 * （SubAgentCard 冒泡 → 本组件 → 宿主，宿主统一处理审批链路）。
 */
import { computed } from 'vue'
import AiReasoning from '../ai-elements/AiReasoning.vue'
import InlineToolCallContent from './InlineToolCallContent.vue'
import ToolCallCard from '../chat/ToolCallCard.vue'
import SubAgentCard from '../chat/SubAgentCard.vue'
import { deriveDisplayStatus } from '../../utils/toolCallStateMachine'
import { mapSubagentsBySpawnToolCall } from '../../utils/subagentAggregation'

const props = defineProps({
  /** 该图层正文（主层 message.content / 子层 subagentContents[threadId].content / 研究任务累计正文） */
  content: {
    type: String,
    default: '',
  },
  /** AI 推理内容（空则不渲染推理区） */
  reasoningContent: {
    type: String,
    default: '',
  },
  /** 推理用时（秒），完成态展示"已思考完成（用时 N 秒）" */
  reasoningDuration: {
    type: Number,
    default: undefined,
  },
  /** 推理进行中（驱动 AiReasoning is-streaming，默认 false） */
  isStreaming: {
    type: Boolean,
    default: false,
  },
  /** 该图层工具调用（主 agent 工具，调用方已用 filterMainToolCalls 过滤+排序） */
  toolCalls: {
    type: Array,
    default: () => [],
  },
  /** 正文引用来源（传给 MarkdownRenderer citations） */
  citations: {
    type: Array,
    default: () => [],
  },
  /** 全量子代理视图数组（spawn 挂载 + orphans 兜底数据源） */
  subagents: {
    type: Array,
    default: () => [],
  },
  /** 已展开的子代理 threadId 集合（宿主统一管理） */
  expandedThreadIds: {
    type: Set,
    default: () => new Set(),
  },
  /** 审批控件置灰（主 agent 工具卡） */
  approvalDisabled: {
    type: Boolean,
    default: false,
  },
  /** 是否渲染孤儿子代理卡（无 spawnToolCallId 的子代理）。仅宿主主层渲染一次：
   *  孤儿语义属于「主 agent 工具流末尾的兜底」，嵌套面板（SubAgentDetailPanel）
   *  内不得重复渲染，否则同一孤儿在多层递归中重复出现（渲染错乱/卸载崩溃）。 */
  showOrphans: {
    type: Boolean,
    default: true,
  },
})

const emit = defineEmits(['toggle-subagent', 'approve', 'reject'])

// spawn 顺序索引：spawnToolCallId → 子代理视图（挂载到对应 spawn 工具之后）；
// orphans 为无 spawnToolCallId 的孤儿（主工具流末尾兜底渲染）
const subagentSpawnIndex = computed(() => mapSubagentsBySpawnToolCall(props.subagents))

/** 取 spawn 工具调用派生的子代理（命中返回单元素数组，未命中返回空数组） */
const spawnedSubagentOf = (toolCall) => {
  const id = toolCall?.id || toolCall?.toolCallId
  if (!id) return []
  const subagent = subagentSpawnIndex.value.bySpawnToolCallId.get(id)
  return subagent ? [subagent] : []
}
</script>

<template>
  <div class="agent-content-pipeline">
    <!-- 推理区（仅当有推理内容时渲染） -->
    <AiReasoning
      v-if="reasoningContent"
      :content="reasoningContent"
      :duration="reasoningDuration"
      :is-streaming="isStreaming"
    />

    <!-- 正文 + 工具内联切段（工具插槽渲染 ToolCallCard + spawn 后 SubAgentCard） -->
    <InlineToolCallContent
      :content="content"
      :tool-calls="toolCalls"
      :citations="citations"
      :approval-disabled="approvalDisabled"
    >
      <template #tool="{ toolCall, approvalDisabled: disabled }">
        <ToolCallCard
          :tool-name="toolCall.name"
          :input="toolCall.input || toolCall.parameters"
          :output="toolCall.output || toolCall.result"
          :status="deriveDisplayStatus(toolCall)"
          :tool-call="toolCall"
          :approval-disabled="disabled"
          :is-subagent-trigger="toolCall.name === 'spawn_sub_agent'"
          @approve="(tc) => emit('approve', tc)"
          @reject="(tc) => emit('reject', tc)"
        />

        <!-- 顺序挂载：spawn 工具之后紧跟其派生的子代理卡片（点击原地展开内容）。
             子代理审批自治：卡片内部按 subagent.status 判断，不受父消息固化状态影响 -->
        <template v-if="toolCall.name === 'spawn_sub_agent'">
          <template v-for="sa in spawnedSubagentOf(toolCall)" :key="sa.threadId">
            <SubAgentCard
              :subagent="sa"
              :expanded="expandedThreadIds.has(sa.threadId)"
              :subagents="subagents"
              :expanded-thread-ids="expandedThreadIds"
              @toggle="(threadId) => emit('toggle-subagent', threadId)"
              @approve="(tc) => emit('approve', tc)"
              @reject="(tc) => emit('reject', tc)"
            />
          </template>
        </template>
      </template>
    </InlineToolCallContent>

    <!-- 孤儿兜底：无 spawnToolCallId 的子代理在主工具流末尾渲染（仅宿主主层） -->
    <template v-if="showOrphans">
      <template v-for="sa in subagentSpawnIndex.orphans" :key="sa.threadId">
        <SubAgentCard
          :subagent="sa"
          :expanded="expandedThreadIds.has(sa.threadId)"
          :subagents="subagents"
          :expanded-thread-ids="expandedThreadIds"
          @toggle="(threadId) => emit('toggle-subagent', threadId)"
          @approve="(tc) => emit('approve', tc)"
          @reject="(tc) => emit('reject', tc)"
        />
      </template>
    </template>
  </div>
</template>

<style scoped>
.agent-content-pipeline {
  width: 100%;
}
</style>
