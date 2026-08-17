<template>
  <el-card class="task-detail-card">
    <template #header>
      <div class="card-header">
        <span>研究任务详情</span>
        <div class="header-actions">
          <el-tag v-if="progressMessage" type="info" class="progress-message">
            {{ progressMessage }}
          </el-tag>
          <el-tag :type="getTaskStatusTagType(task.status)">
            {{ getTaskStatusText(task.status) }}
          </el-tag>
          <el-button link type="primary" size="small" @click="emit('back')">
            返回列表
          </el-button>
        </div>
      </div>
    </template>

    <el-descriptions :column="2" border>
      <el-descriptions-item label="任务ID">{{ task.taskId }}</el-descriptions-item>
      <el-descriptions-item label="创建时间">{{ formatDate(task.createdAt) }}</el-descriptions-item>
      <el-descriptions-item label="研究主题" :span="2">{{ task.query }}</el-descriptions-item>
      <el-descriptions-item label="网络搜索">
        <el-tag :type="task.enableWebSearch ? 'success' : 'info'">
          {{ task.enableWebSearch ? '已启用' : '未启用' }}
        </el-tag>
      </el-descriptions-item>
      <el-descriptions-item label="文档分析">
        <el-tag :type="task.enableDocAnalysis ? 'success' : 'info'">
          {{ task.enableDocAnalysis ? '已启用' : '未启用' }}
        </el-tag>
      </el-descriptions-item>
      <el-descriptions-item label="来源">
        <el-tag v-if="task.source === 'chat'" type="primary">
          <el-icon style="vertical-align: middle; margin-right: 4px;"><ChatDotRound /></el-icon>
          聊天触发
        </el-tag>
        <el-tag v-else type="info">独立研究</el-tag>
      </el-descriptions-item>
      <el-descriptions-item
        v-if="task.enableDocAnalysis && task.knowledgeBaseIds && task.knowledgeBaseIds.length"
        label="关联知识库"
        :span="2"
      >
        <el-tag
          v-for="kbId in task.knowledgeBaseIds"
          :key="kbId"
          size="small"
          class="kb-tag"
        >
          {{ kbId }}
        </el-tag>
      </el-descriptions-item>
    </el-descriptions>

    <div
      v-if="task.status === ResearchTaskStatus.RUNNING
        || task.status === ResearchTaskStatus.PENDING
        || task.status === ResearchTaskStatus.AWAITING_APPROVAL"
      class="progress-section"
    >
      <el-progress
        :percentage="progressPercentage"
        :status="task.status === ResearchTaskStatus.PENDING ? '' : undefined"
        :stroke-width="8"
        striped
        striped-flow
      />
      <p class="progress-hint">深度研究通常需要 5-10 分钟，请耐心等待...</p>
    </div>

    <!-- AI 推理面板（框架接入：绑定 stream_reasoning WebSocket 事件） -->
    <!-- 深度研究模块推理区文案固定"研究推理"（spec D4：研究语境优先于深度思考开关） -->
    <AiReasoning
      v-if="reasoning && reasoning.content"
      :content="reasoning.content"
      :duration="reasoning.duration"
      :is-streaming="task.status === ResearchTaskStatus.RUNNING || task.status === ResearchTaskStatus.PENDING"
      :source="reasoning.source || 'deep_thinking'"
      reasoning-label="研究推理"
      style="margin-top: 16px;"
    />

    <!-- 研究过程：工具调用内联切段（渲染结构完全照 ChatMessage，content 传空，
         深度研究主 agent 无独立过程正文，主输出 final_report 由下方报告组件独立展示） -->
    <InlineToolCallContent
      v-if="mainToolCalls.length > 0"
      :content="''"
      :tool-calls="mainToolCalls"
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
          @approve="(t) => emit('approve', t)"
          @reject="(t) => emit('reject', t)"
        />

        <!-- 顺序挂载：spawn 工具之后紧跟其派生的子代理卡片（点击原地展开内容） -->
        <template v-if="toolCall.name === 'spawn_sub_agent'">
          <template v-for="sa in spawnedSubagentOf(toolCall)" :key="sa.threadId">
            <SubAgentCard
              :subagent="sa"
              :expanded="expandedSubagentThreadIds.has(sa.threadId)"
              reasoning-label="研究推理"
              :subagents="subagents"
              :expanded-thread-ids="expandedSubagentThreadIds"
              @toggle="toggleSubagent"
              @approve="(t) => emit('approve', t)"
              @reject="(t) => emit('reject', t)"
            />
          </template>
        </template>
      </template>
    </InlineToolCallContent>

    <!-- 孤儿兜底：无 spawnToolCallId 的子代理在正文末尾渲染 -->
    <template v-for="sa in subagentSpawnIndex.orphans" :key="sa.threadId">
      <SubAgentCard
        :subagent="sa"
        :expanded="expandedSubagentThreadIds.has(sa.threadId)"
        reasoning-label="研究推理"
        :subagents="subagents"
        :expanded-thread-ids="expandedSubagentThreadIds"
        @toggle="toggleSubagent"
        @approve="(t) => emit('approve', t)"
        @reject="(t) => emit('reject', t)"
      />
    </template>

    <ResearchTaskReport
      :task="task"
      :doc-analysis-file="docAnalysisFile"
      :doc-analysis-content="docAnalysisContent"
      :doc-analysis-loading="docAnalysisLoading"
      @view-task="(val) => emit('view-task', val)"
      @open-continue-dialog="(val) => emit('open-continue-dialog', val)"
      @open-in-chat="emit('open-in-chat')"
      @load-doc-analysis="emit('load-doc-analysis')"
    />
  </el-card>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { ChatDotRound } from '@element-plus/icons-vue'
import SubAgentCard from '@/components/chat/SubAgentCard.vue'
import ToolCallCard from '@/components/chat/ToolCallCard.vue'
import InlineToolCallContent from '@/components/common/InlineToolCallContent.vue'
import { deriveDisplayStatus } from '@/utils/toolCallStateMachine'
import { buildSubagentsFromMessage, mapSubagentsBySpawnToolCall } from '@/utils/subagentAggregation'
import { useSubagents, subagentsMetaMap } from '@/composables/useSubagents'
import { SUBAGENT_STATUS } from '@/utils/subagentStatus'
import settings from '@/config/settings'
import ResearchTaskReport from './ResearchTaskReport.vue'
import AiReasoning from '@/components/ai-elements/AiReasoning.vue'
import { formatDate } from '@/utils/format'
import { getTaskStatusText, getTaskStatusTagType } from '@/utils/researchTaskStatus'
import { isApprovalDisabled } from '@/utils/approvalGate'
import { ResearchTaskStatus } from '@/types'

/**
 * 深度研究 - 任务详情卡片
 * 包含：任务描述 / 进度条 / 审批面板 / 报告区（嵌入 ResearchTaskReport）
 *
 * 任务状态文案 / 标签色统一走 utils/researchTaskStatus.js（getTaskStatusText /
 * getTaskStatusTagType），与 TaskList.vue / AiNode.vue 共用单一权威。
 */
const props = defineProps({
  /** 当前任务对象 */
  task: {
    type: Object,
    required: true,
  },
  /** 进度提示消息（SSE 推送） */
  progressMessage: {
    type: String,
    default: '',
  },
  /** AI 推理内容（来自 stream_reasoning 事件） */
  reasoning: {
    type: Object,
    default: null,
  },
  /** 进度百分比 */
  progressPercentage: {
    type: Number,
    default: 0,
  },
  /** 当前任务的工具调用历史数组（全量扁平，含主/子代理） */
  toolCalls: {
    type: Array,
    default: () => [],
  },
  /** 子代理正文/思考索引（spec D10）：
   *  { [subagentThreadId]: { content, reasoningContent } } */
  subagentContents: {
    type: Object,
    default: () => ({}),
  },
  /** 文档分析文件路径 */
  docAnalysisFile: {
    type: String,
    default: null,
  },
  /** 文档分析内容 */
  docAnalysisContent: {
    type: String,
    default: null,
  },
  /** 文档分析加载中 */
  docAnalysisLoading: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits([
  'back',
  'view-task',
  'open-continue-dialog',
  'open-in-chat',
  'load-doc-analysis',
  'approve',
  'reject',
])

// 子代理渲染（spec D10：摘要卡片 + 独立展开，替代 AgentLayerCard 递归）。
// 深度研究模块子代理按 parent_thread_id = taskId 关联（无 assistantMessageId 分消息）。
const { fetchSubagents } = useSubagents()

// 主 agent 工具调用（subagentThreadId 为空）
const mainToolCalls = computed(() =>
  (Array.isArray(props.toolCalls) ? props.toolCalls : []).filter(tc => !tc.subagentThreadId)
)

// 审批控件置灰（统一 chat 语义：任务终态视为固化，其余可交互）
const approvalDisabled = computed(() =>
  isApprovalDisabled({
    isFinalized:
      props.task?.status === ResearchTaskStatus.COMPLETED ||
      props.task?.status === ResearchTaskStatus.FAILED,
  })
)

// 该任务下子代理元数据列表：从模块级响应式缓存按 parentThreadId 过滤
// （fetchSubagents / scheduleSubagentsRefresh 刷新后自动更新，双浏览器一致）
const subagentMetaList = computed(() => {
  const taskId = props.task?.taskId
  if (!taskId) return []
  return Object.values(subagentsMetaMap.value).filter(
    (sa) => sa && sa.parentThreadId === taskId
  )
})

// 子代理聚合视图
const subagents = computed(() =>
  buildSubagentsFromMessage(
    { toolCalls: props.toolCalls, subagentContents: props.subagentContents },
    subagentMetaList.value
  )
)

// spawn 顺序索引：spawnToolCallId → 子代理视图（挂载到对应 spawn 工具之后）；
// orphans 为无 spawnToolCallId 的孤儿（主工具列表末尾兜底渲染）
const subagentSpawnIndex = computed(() => mapSubagentsBySpawnToolCall(subagents.value))

/** 取 spawn 工具调用派生的子代理（命中返回单元素数组，未命中返回空数组） */
const spawnedSubagentOf = (toolCall) => {
  const id = toolCall?.id || toolCall?.toolCallId
  if (!id) return []
  const subagent = subagentSpawnIndex.value.bySpawnToolCallId.get(id)
  return subagent ? [subagent] : []
}

// 展开的子代理 threadId 集合
const expandedSubagentThreadIds = ref(new Set())

const toggleSubagent = (threadId) => {
  const next = new Set(expandedSubagentThreadIds.value)
  if (next.has(threadId)) next.delete(threadId)
  else next.add(threadId)
  expandedSubagentThreadIds.value = next
}

// spec D9：autoExpandPendingConfirm 开启时，仅「等待你的确认」的卡片自动展开（嵌套内层永不自动展开）
watch(subagents, (list) => {
  if (!settings.autoExpandPendingConfirm) return
  const next = new Set(expandedSubagentThreadIds.value)
  let changed = false
  for (const sa of list) {
    if (sa.status === SUBAGENT_STATUS.INTERRUPTED_PENDING_USER_INPUT && !next.has(sa.threadId)) {
      next.add(sa.threadId)
      changed = true
    }
  }
  if (changed) expandedSubagentThreadIds.value = next
})

// 任务 ID 变化时拉取元数据（幂等合并到模块级缓存；后续子代理工具/审批事件
// 由 toolCallHandler 触发 scheduleSubagentsRefresh 增量刷新，无需组件轮询）
watch(
  () => props.task?.taskId,
  (taskId) => {
    if (taskId) {
      fetchSubagents(taskId)
    }
  },
  { immediate: true }
)

// 子代理内部工具审批：与主 agent 完全一致，emit('approve'/'reject') 冒泡到
// DeepResearchView.handleApprove/handleReject → approvalStore.executeApproval
// → 统一审批端点 /approvals/{interrupt_id}/resume/（gateway 按 extra.
// subagent_thread_id 路由到子代理恢复）。本组件不做任何审批分支处理。

</script>

<style scoped>
.task-detail-card {
  margin-bottom: 20px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.progress-message {
  animation: pulse-opacity 2s ease-in-out infinite;
}

@keyframes pulse-opacity {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.6; }
}

.progress-section {
  margin-top: 24px;
  padding: 16px;
  background: var(--el-fill-color-lighter);
  border-radius: 8px;
}

.progress-hint {
  margin: 12px 0 0 0;
  font-size: 13px;
  color: var(--el-text-color-secondary);
  text-align: center;
}

.kb-tag {
  margin-right: 6px;
  margin-bottom: 4px;
}

@media (max-width: 768px) {
  .card-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }

  .header-actions {
    width: 100%;
    flex-wrap: wrap;
  }

  .progress-section {
    padding: 12px;
  }
}

@media (max-width: 480px) {
  .card-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }

  .header-actions {
    flex-wrap: wrap;
    gap: 8px;
  }
}
</style>
