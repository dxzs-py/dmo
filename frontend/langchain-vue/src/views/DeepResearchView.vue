<template>
  <div class="deep-research-view">
    <div class="view-content">
      <el-card class="start-card">
        <template #header>
          <div class="card-header">
            <span class="page-title">深度研究</span>
          </div>
        </template>
        <el-form :model="researchForm" label-width="120px" @submit.prevent>
          <el-form-item label="研究主题">
            <el-input
              v-model="researchForm.query"
              type="textarea"
              :rows="3"
              placeholder="请输入您想研究的主题..."
            />
          </el-form-item>
          <el-form-item label="启用网络搜索">
            <el-switch v-model="researchForm.enableWebSearch" />
          </el-form-item>
          <el-form-item label="选择知识库">
            <div class="kb-selector">
              <div class="kb-selector-header">
                <el-input
                  v-model="kbSearchQuery"
                  placeholder="搜索知识库..."
                  clearable
                  size="small"
                  class="kb-search-input"
                  @input="filterKnowledgeBases"
                />
                <el-button size="small" :loading="kbLoading" @click="refreshKnowledgeBases">
                  刷新
                </el-button>
              </div>
              <div v-if="kbLoading" class="kb-loading">
                <el-icon class="is-loading"><Loading /></el-icon>
                <span>加载知识库列表...</span>
              </div>
              <div v-else-if="filteredKnowledgeBases.length === 0" class="kb-empty">
                <span v-if="kbSearchQuery">未找到匹配的知识库</span>
                <span v-else>暂无可用知识库，请先在知识库页面创建并上传文档</span>
              </div>
              <div v-else class="kb-list">
                <el-checkbox-group v-model="researchForm.knowledgeBaseIds">
                  <div
                    v-for="kb in filteredKnowledgeBases"
                    :key="kb.id"
                    class="kb-item"
                  >
                    <el-checkbox :label="kb.id" :value="kb.id">
                      <div class="kb-item-content">
                        <span class="kb-name">{{ kb.name }}</span>
                        <span class="kb-meta">
                          <el-tag size="small" type="info">{{ kb.chunkCount || 0 }} 文档块</el-tag>
                          <span v-if="kb.description" class="kb-desc">{{ kb.description }}</span>
                        </span>
                      </div>
                    </el-checkbox>
                  </div>
                </el-checkbox-group>
              </div>
              <div v-if="researchForm.knowledgeBaseIds.length > 0" class="kb-selected-summary">
                已选择 {{ researchForm.knowledgeBaseIds.length }} 个知识库
              </div>
            </div>
          </el-form-item>
          <el-form-item label="选择模型">
            <ModelSelector @change="onModelChange" />
          </el-form-item>
          <el-form-item label="深度思考">
            <el-switch
              :model-value="useDeepThinking"
              :disabled="!modelSupportsDeepThinking"
              @change="useDeepThinking = $event"
            />
            <span v-if="!modelSupportsDeepThinking" class="deep-thinking-hint">
              当前模型不支持深度思考
            </span>
          </el-form-item>
          <el-form-item label="工具选择">
            <div class="tool-selector-wrapper">
              <ToolSelector
                :model-value="researchForm.selectedTools"
                @update:model-value="(val) => researchForm.selectedTools = val"
                @update:selected-mcp-servers="(val) => researchForm.selectedMcpServers = val"
                @update:use-mcp="(val) => researchForm.useMcp = val"
              />
              <span v-if="researchForm.selectedTools.length > 0" class="tool-selected-hint">
                已选择 {{ researchForm.selectedTools.length }} 个工具
              </span>
            </div>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="isLoading" @click="startResearch">
              开始研究
            </el-button>
          </el-form-item>
        </el-form>
      </el-card>

      <ResearchTaskDetail
        v-if="showTaskDetail && task"
        :task="task"
        :main-content="taskContent"
        :progress-message="progressMessage"
        :progress-percentage="progressPercentage"
        :tool-calls="taskToolCalls"
        :subagent-contents="taskSubagentContents"
        :doc-analysis-file="docAnalysisFile"
        :doc-analysis-content="docAnalysisContent"
        :doc-analysis-loading="docAnalysisLoading"
        :reasoning="reasoning"
        @back="showTaskDetail = false"
        @view-task="viewTask"
        @open-continue-dialog="openContinueDialog"
        @open-in-chat="openInChat"
        @load-doc-analysis="loadDocAnalysis"
        @approve="handleApprove"
        @reject="handleReject"
      />

      <el-card v-else class="task-list-card">
        <template #header>
          <div class="card-header">
            <span>历史任务</span>
            <el-button text type="primary" @click="showFileSearch = !showFileSearch">
              {{ showFileSearch ? '收起搜索' : '全局文件搜索' }}
            </el-button>
          </div>
        </template>

        <div v-if="showFileSearch" class="file-search-section">
          <el-input
            v-model="fileSearchQuery"
            placeholder="搜索所有研究任务的文件..."
            clearable
            @keyup.enter="handleFileSearch"
          >
            <template #append>
              <el-button :loading="fileSearchLoading" @click="handleFileSearch">搜索</el-button>
            </template>
          </el-input>
          <div v-if="fileSearchResults.length" class="file-search-results">
            <el-table :data="fileSearchResults" style="width: 100%" size="small">
              <el-table-column prop="filename" label="文件名" />
              <el-table-column prop="taskId" label="任务ID" width="160" />
              <el-table-column prop="size" label="大小" width="100">
                <template #default="scope">
                  {{ scope.row.size ? formatFileSize(scope.row.size) : '-' }}
                </template>
              </el-table-column>
              <el-table-column label="操作" width="100">
                <template #default="scope">
                  <el-button link type="primary" size="small" @click="viewTask({ taskId: scope.row.taskId })">
                    查看任务
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
          </div>
          <el-empty v-else-if="fileSearchSearched && !fileSearchLoading" description="未找到匹配的文件" :image-size="60" />
        </div>

        <TaskList
          ref="taskListRef"
          module-type="deep-research"
          :api="deepResearchAPI"
          :status-options="statusOptions"
          @view-task="viewTask"
          @delete-task="deleteTask"
          @continue-task="handleContinueTask"
        />
      </el-card>
    </div>

    <el-dialog v-model="continueDialogVisible" title="继续研究" width="600px" :close-on-click-modal="false">
      <p style="margin-bottom: 12px; color: var(--el-text-color-secondary)">
        基于已有研究继续深入探索
      </p>
      <el-descriptions :column="1" border size="small" style="margin-bottom: 16px">
        <el-descriptions-item label="原研究主题">
          {{ continueParentTask?.query?.substring(0, 100) }}
        </el-descriptions-item>
        <el-descriptions-item label="版本">
          v{{ continueParentTask?.version || 1 }} → v{{ (continueParentTask?.version || 1) + 1 }}
        </el-descriptions-item>
      </el-descriptions>
      <el-form :model="continueForm" label-width="100px" @submit.prevent>
        <el-form-item label="补充说明">
          <el-input
            v-model="continueForm.additionalQuery"
            type="textarea"
            :rows="3"
            placeholder="描述你想继续探索的方向（可选）..."
          />
        </el-form-item>
        <el-form-item label="选择模型">
          <ModelSelector @change="onContinueModelChange" />
        </el-form-item>
        <el-form-item label="深度思考">
          <el-switch
            :model-value="useDeepThinking"
            :disabled="!modelSupportsDeepThinking"
            @change="useDeepThinking = $event"
          />
          <span v-if="!modelSupportsDeepThinking" class="deep-thinking-hint">
            当前模型不支持深度思考
          </span>
        </el-form-item>
        <el-form-item label="启用网络搜索">
          <el-switch v-model="continueForm.enableWebSearch" />
        </el-form-item>
        <el-form-item label="选择知识库">
          <div class="kb-selector">
            <div v-if="filteredKnowledgeBases.length === 0" class="kb-empty">
              <span>暂无可用知识库</span>
            </div>
            <div v-else class="kb-list">
              <el-checkbox-group v-model="continueForm.knowledgeBaseIds">
                <div v-for="kb in filteredKnowledgeBases" :key="kb.id" class="kb-item">
                  <el-checkbox :label="kb.name" :value="kb.id">
                    <div class="kb-item-content">
                      <span class="kb-name">{{ kb.name }}</span>
                      <span class="kb-meta">
                        <el-tag size="small" type="info">{{ kb.chunkCount || 0 }} 文档块</el-tag>
                      </span>
                    </div>
                  </el-checkbox>
                </div>
              </el-checkbox-group>
            </div>
          </div>
        </el-form-item>
        <el-form-item label="工具选择">
          <div class="tool-selector-wrapper">
            <ToolSelector
              :model-value="continueForm.selectedTools"
              @update:model-value="(val) => continueForm.selectedTools = val"
              @update:selected-mcp-servers="(val) => continueForm.selectedMcpServers = val"
              @update:use-mcp="(val) => continueForm.useMcp = val"
            />
            <span v-if="continueForm.selectedTools.length > 0" class="tool-selected-hint">
              已选择 {{ continueForm.selectedTools.length }} 个工具
            </span>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="continueDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="isLoading" @click="submitContinueResearch">
          开始续研
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, onUnmounted, onActivated, onDeactivated, provide } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { deepResearchAPI } from '@/api/research'
import { knowledgeAPI } from '@/api/knowledge'
import { readSSEStream } from '../utils/sse'
import { extractSSEError } from '../utils/apiErrorHandler'
import { ElMessage } from 'element-plus'
import { Loading } from '@element-plus/icons-vue'
import TaskList from '../components/chat/TaskList.vue'
import ModelSelector from '../components/common/ModelSelector.vue'
import ToolSelector from '../components/chat/ToolSelector.vue'
import ResearchTaskDetail from '../components/research/ResearchTaskDetail.vue'
import { useResearchSettingsStore } from '../stores/researchSettings'
import { useSessionStore } from '../stores/session'
import { useApprovalStore } from '../stores/approval'
import { useResearchStore } from '../stores/research'
import { formatFileSize, getQueryParam } from '../utils/format'
import { logger } from '../utils/logger'
import { getInterruptId } from '../utils/messageOperations'
import { useApiTask } from '@/composables/useApiTask'
import { useTaskRealtimeSync } from '@/composables/useTaskRealtimeSync'
import { useTaskListRealtimeSync } from '@/composables/useTaskListRealtimeSync'
import { ResearchTaskStatus } from '@/types'

// 根因 C 解耦：深度研究模块使用独立设置 store，
// 与聊天模块的全局 modelStore（模型/深度思考/参数）完全独立
const researchSettings = useResearchSettingsStore()
// 通过 provide/inject 传递给 ModelSelector（避免 Pinia store 经 props 代理导致渲染异常）
provide('modelSettingsStore', researchSettings)
const approvalStore = useApprovalStore()
const researchStore = useResearchStore()

const router = useRouter()
const route = useRoute()
const currentTaskId = ref(null)
const task = computed(() => researchStore.getTaskStatus(currentTaskId.value))

/** 当前任务推理内容（来自 stream_reasoning WebSocket 事件） */
const reasoning = computed(() => task.value?.reasoning || null)

/** 当前任务的工具调用历史列表 */
const taskToolCalls = computed(() => {
  const currentTask = task.value
  if (!currentTask?.taskId) return []

  // 优先从 sessionStore 读取（聊天触发的深度研究，有 session_id 关联）
  const chatSessionId = currentTask.sessionId || currentTask.chatSessionId
  if (chatSessionId) {
    const sessionStore = useSessionStore()
    const messages = sessionStore.getSessionMessages(chatSessionId)
    // 精确匹配当前深度研究任务关联的消息（researchTaskId === taskId），
    // 而非"第一个带 toolCalls 的消息"——避免同会话其他消息
    // （如代理模式的工具调用）污染深度研究详情。
    const researchMsg = [...messages].reverse().find(msg => msg.researchTaskId === currentTask.taskId)
    if (researchMsg?.toolCalls && researchMsg.toolCalls.length > 0) {
      return researchMsg.toolCalls
    }
  }

  // 回退到 researchStore（独立深度研究任务）
  return researchStore.getToolCalls(currentTask.taskId)
})

/** 主 agent 累计正文（与 taskToolCalls 同源，session 消息优先，researchStore 回退）：
 * 聊天触发的深度研究，正文权威源是关联 ChatMessage.content（实时写入）；
 * researchStore.taskInfo.content 依赖 task 频道 stream_content_update 事件，
 * 详情页晚订阅时可能缺失 → position 切段全部退化为"追加末尾"按 position 重排导致乱序。 */
const taskContent = computed(() => {
  const currentTask = task.value
  if (!currentTask?.taskId) return ''
  const chatSessionId = currentTask.sessionId || currentTask.chatSessionId
  if (chatSessionId) {
    const sessionStore = useSessionStore()
    const messages = sessionStore.getSessionMessages(chatSessionId)
    const researchMsg = [...messages].reverse().find(msg => msg.researchTaskId === currentTask.taskId)
    if (researchMsg?.content) return researchMsg.content
  }
  return currentTask.content || ''
})

/** 当前任务的子代理图层正文/思考索引（Agent 图层嵌套规范 Task 8.3）：
 *  与 taskToolCalls 同源（session 消息优先，researchStore 回退），
 *  保证图层树正文与工具归属一致。 */
const taskSubagentContents = computed(() => {
  const currentTask = task.value
  if (!currentTask?.taskId) return {}

  const chatSessionId = currentTask.sessionId || currentTask.chatSessionId
  if (chatSessionId) {
    const sessionStore = useSessionStore()
    const messages = sessionStore.getSessionMessages(chatSessionId)
    const researchMsg = [...messages].reverse().find(msg => msg.researchTaskId === currentTask.taskId)
    if (researchMsg?.subagentContents && typeof researchMsg.subagentContents === 'object') {
      return researchMsg.subagentContents
    }
  }

  return researchStore.getTaskSubagentContents(currentTask.taskId)
})
const showTaskDetail = ref(false)
const taskListRef = ref(null)
const progressMessage = ref('')
const elapsedSeconds = ref(0)
const showFileSearch = ref(false)
const fileSearchQuery = ref('')
const fileSearchResults = ref([])
const fileSearchSearched = ref(false)

const knowledgeBases = ref([])
const kbSearchQuery = ref('')
const filteredKnowledgeBases = ref([])

const docAnalysisContent = ref(null)
const docAnalysisFile = ref(null)

const continueDialogVisible = ref(false)
const continueParentTask = ref(null)
const continueForm = reactive({
  additionalQuery: '',
  enableWebSearch: true,
  knowledgeBaseIds: [],
  providerId: null,
  modelName: null,
  useMcp: false,
  selectedMcpServers: [],
  selectedTools: [],
})

let pollingTimer = null
let sseAbortController = null
let sseReaderActive = false
let elapsedTimer = null
let pollCount = 0
let currentPollInterval = 3000
const MAX_POLL_COUNT = 600
const BASE_POLL_INTERVAL = 3000
const MAX_POLL_INTERVAL = 30000
const POLL_BACKOFF_FACTOR = 1.5

const progressPercentage = computed(() => {
  if (!task.value) return 0
  if (task.value.status === ResearchTaskStatus.COMPLETED) return 100
  if (task.value.status === ResearchTaskStatus.FAILED) return 0
  if (task.value.status === ResearchTaskStatus.AWAITING_APPROVAL) return 50
  if (task.value.status === ResearchTaskStatus.PENDING) return 10
  if (task.value.status === ResearchTaskStatus.RUNNING) {
    const maxSeconds = 600
    const pct = Math.min(90, 10 + (elapsedSeconds.value / maxSeconds) * 80)
    return Math.round(pct)
  }
  return 0
})

const researchForm = reactive({
  query: '',
  enableWebSearch: true,
  knowledgeBaseIds: [],
  providerId: null,
  modelName: null,
  useMcp: false,
  selectedMcpServers: [],
  selectedTools: [],
})

const useDeepThinking = computed({
  get: () => researchSettings.thinkingEnabled,
  set: (val) => {
    const paramCfg = researchSettings.currentProviderSpecialParams?.thinking
    if (!paramCfg) return
    researchSettings.setSpecialParam('thinking', val ? paramCfg.enabledValue : paramCfg.disabledValue)
  },
})

const statusOptions = [
  { value: ResearchTaskStatus.PENDING, label: '待执行' },
  { value: ResearchTaskStatus.RUNNING, label: '执行中' },
  { value: ResearchTaskStatus.AWAITING_APPROVAL, label: '等待审批' },
  { value: ResearchTaskStatus.COMPLETED, label: '已完成' },
  { value: ResearchTaskStatus.FAILED, label: '失败' },
]

const modelSupportsDeepThinking = computed(() => {
  return researchSettings.currentModelCapabilities.includes('deep_thinking')
})

const onModelChange = ({ providerId, modelName }) => {
  researchForm.providerId = providerId
  researchForm.modelName = modelName
}

const {
  run: runRefreshKnowledgeBases,
  loading: kbLoading,
} = useApiTask(
  () => knowledgeAPI.getKnowledgeBases(),
  {
    showErrorToast: false,
    onSuccess: (response) => {
      const data = response.data?.data || response.data
      knowledgeBases.value = data?.items || []
      filterKnowledgeBases()
    },
    onError: (error) => {
      logger.error('加载知识库列表失败:', error)
      ElMessage.error('加载知识库列表失败')
    },
  }
)

const refreshKnowledgeBases = () => runRefreshKnowledgeBases()

const filterKnowledgeBases = () => {
  const query = kbSearchQuery.value.toLowerCase().trim()
  if (!query) {
    filteredKnowledgeBases.value = [...knowledgeBases.value]
  } else {
    filteredKnowledgeBases.value = knowledgeBases.value.filter(
      kb => kb.name?.toLowerCase().includes(query) || kb.description?.toLowerCase().includes(query)
    )
  }
}

// 查找任务的分析文件（失败返回 undefined，调用方按 falsy 判断兼容原 null 语义）
const {
  run: runFindDocAnalysisFile,
} = useApiTask(
  async () => {
    if (!task.value?.taskId) return null
    const res = await deepResearchAPI.getFiles(task.value.taskId)
    const data = res.data?.data || res.data
    const files = data?.files || data || []
    const notesDir = files.find(f => f.name === 'notes' && f.type === 'directory')
    if (notesDir && notesDir.children) {
      const mdFile = notesDir.children.find(f => f.name?.endsWith('.md'))
      if (mdFile) return `notes/${mdFile.name}`
      const txtFile = notesDir.children.find(f => f.name?.endsWith('.txt'))
      if (txtFile) return `notes/${txtFile.name}`
    }
    const mdFiles = files.filter(f => f.type === 'file' && f.name?.endsWith('.md') && !f.name?.includes('report'))
    if (mdFiles.length > 0) return mdFiles[0].relativePath || mdFiles[0].name
    const rootNotes = files.filter(f => f.type === 'file' && f.name?.endsWith('.txt'))
    if (rootNotes.length > 0) return rootNotes[0].relativePath || rootNotes[0].name
    return null
  },
  {
    loading: false,
    showErrorToast: false,
  }
)

const {
  run: runLoadDocAnalysis,
  loading: docAnalysisLoading,
} = useApiTask(
  async () => {
    const file = await runFindDocAnalysisFile()
    if (file) {
      const response = await deepResearchAPI.getFileContent(task.value.taskId, file)
      const data = response.data?.data || response.data
      docAnalysisContent.value = data?.content || data || ''
      docAnalysisFile.value = file
    } else {
      docAnalysisContent.value = null
    }
  },
  {
    showErrorToast: false,
    onError: (error) => {
      logger.warn('加载文档分析详情失败:', error)
      docAnalysisContent.value = null
    },
  }
)

const loadDocAnalysis = () => {
  if (!task.value?.taskId || !task.value?.knowledgeBaseIds?.length) return
  return runLoadDocAnalysis()
}

const checkDocAnalysisFile = () => {
  docAnalysisFile.value = null
  docAnalysisContent.value = null
}

const autoLoadDocAnalysis = async () => {
  if (!task.value?.taskId) return
  if (!task.value?.enableDocAnalysis) return
  await loadDocAnalysis()
}

// 实时审批事件由 WebSocket 处理（syncStore.handleRealtimeEvent），
// SSE 仅处理 approval_history（初始历史加载），故不再需要本地 handleApprovalEvent 桥接。

/** 审批执行成功标记：executeApproval 从不 rethrow（业务失败内部消化后正常 resolve），
 * 用显式标记区分 resolve 与 reject（后者仅在 store 内部代码崩溃时发生） */
const APPROVAL_EXECUTED = 'approval-executed'

const {
  run: runExecuteApproval,
  error: approvalError,
} = useApiTask(
  async ({ approval, approved, userInput, options }) => {
    await approvalStore.executeApproval(approval, approved, userInput, options)
    return APPROVAL_EXECUTED
  },
  {
    loading: false,
    showErrorToast: false,
  }
)

/** 审批执行失败后恢复审批状态（executeApproval 内部 catch 已恢复 pendingApprovals，此处同步 session store） */
const restoreApprovalState = (approval, interruptId) => {
  const sessionStore = useSessionStore()
  const sid = sessionStore.currentSessionId
  if (sid) {
    sessionStore.updateToolCallApprovalState(sid, interruptId, 'pending')
    sessionStore.setApprovalToLastMessage(sid, { ...approval, state: 'pending' })
  }
}

// 确认审批（ToolCallCard emit 的参数是 toolCall 对象）
const handleApprove = async (toolCallData) => {
  const approval = toolCallData.approval || toolCallData
  const interruptId = getInterruptId(approval) || toolCallData.id
  const userInput = toolCallData._userInput

  // 防重复：如果正在处理中，忽略（executeApproval 内部也有防重复，此处提前拦截避免无效调用）
  const entry = approvalStore.pendingApprovals.get(interruptId)
  if (entry?.approvalData?.state === 'processing') return

  const result = await runExecuteApproval({
    approval,
    approved: true,
    userInput,
    options: { taskId: task.value?.taskId },
  })
  if (result === APPROVAL_EXECUTED) {
    ElMessage.success('已确认操作')
  } else if (approvalError.value) {
    restoreApprovalState(approval, interruptId)
    ElMessage.error('确认操作失败')
  }
}

// 拒绝审批（ToolCallCard emit 的参数是 toolCall 对象）
const handleReject = async (toolCallData) => {
  const approval = toolCallData.approval || toolCallData
  const interruptId = getInterruptId(approval) || toolCallData.id

  // 防重复：如果正在处理中，忽略
  const entry = approvalStore.pendingApprovals.get(interruptId)
  if (entry?.approvalData?.state === 'processing') return

  const result = await runExecuteApproval({
    approval,
    approved: false,
    options: { taskId: task.value?.taskId },
  })
  if (result === APPROVAL_EXECUTED) {
    ElMessage.info('已拒绝操作')
  } else if (approvalError.value) {
    restoreApprovalState(approval, interruptId)
    ElMessage.error('拒绝操作失败')
  }
}

const isTerminalStatus = (s) => s === ResearchTaskStatus.COMPLETED || s === ResearchTaskStatus.FAILED

const pollTaskStatus = async () => {
  if (!task.value || isTerminalStatus(task.value.status)) {
    stopPolling()
    return
  }

  pollCount++
  if (pollCount >= MAX_POLL_COUNT) {
    ElMessage.warning('研究任务轮询超时，请刷新页面查看最新状态')
    stopPolling()
    return
  }

  try {
    const response = await deepResearchAPI.getStatus(task.value.taskId)
    const responseData = response.data.data || response.data
    const prevStatus = task.value.status
    researchStore.setTaskStatus(currentTaskId.value, responseData)

    if (isTerminalStatus(task.value.status)) {
      stopPolling()
      stopElapsedTimer()
      checkDocAnalysisFile()
      autoLoadDocAnalysis()
      return
    }

    if (prevStatus === ResearchTaskStatus.PENDING && task.value.status === ResearchTaskStatus.RUNNING) {
      currentPollInterval = BASE_POLL_INTERVAL
    } else {
      currentPollInterval = Math.min(
        Math.floor(currentPollInterval * POLL_BACKOFF_FACTOR),
        MAX_POLL_INTERVAL
      )
    }

    pollingTimer = setTimeout(pollTaskStatus, currentPollInterval)
  } catch (error) {
    logger.error('获取任务状态失败:', error)
    if (error?.response?.status === 404) {
      researchStore.setTaskStatus(currentTaskId.value, { status: ResearchTaskStatus.FAILED, errorMessage: '研究任务不存在或已被删除' })
      stopPolling()
      return
    }
    currentPollInterval = Math.min(
      Math.floor(currentPollInterval * POLL_BACKOFF_FACTOR),
      MAX_POLL_INTERVAL
    )
    pollingTimer = setTimeout(pollTaskStatus, currentPollInterval)
  }
}

const stopPolling = () => {
  if (pollingTimer) {
    clearTimeout(pollingTimer)
    pollingTimer = null
  }
  currentPollInterval = BASE_POLL_INTERVAL
}

const {
  run: runStartResearch,
  loading: startResearchLoading,
} = useApiTask(
  async () => {
    const modelConfig = researchSettings.getModelConfig()
    const response = await deepResearchAPI.start({
      query: researchForm.query,
      enableWebSearch: researchForm.enableWebSearch,
      enableDocAnalysis: researchForm.knowledgeBaseIds.length > 0,
      knowledgeBaseIds: researchForm.knowledgeBaseIds,
      useMcp: researchForm.useMcp,
      selectedMcpServers: researchForm.selectedMcpServers,
      selectedTools: researchForm.selectedTools,
      providerId: researchForm.providerId || modelConfig.providerId,
      modelName: researchForm.modelName || modelConfig.modelName,
      enableDeepThinking: researchSettings.thinkingEnabled,
      temperature: modelConfig.temperature,
      maxTokens: modelConfig.maxTokens,
      specialParams: modelConfig.specialParams,
    })
    return response.data.data || response.data
  },
  {
    showErrorToast: false,
    onSuccess: (taskData) => {
      currentTaskId.value = taskData.taskId
      researchStore.setTaskStatus(currentTaskId.value, taskData, { force: true })
      showTaskDetail.value = true
      ElMessage.success('研究任务已启动')

      stopPolling()
      startElapsedTimer()
      // 启动新任务后立即订阅 WebSocket 实时事件，避免在用户切走再回来前丢失事件
      subscribeRealtimeForTask(task.value)
      connectSSE(task.value.taskId)
    },
    onError: (error) => {
      logger.error('启动研究任务失败:', error)
      const detail = error.response?.data?.data || error.response?.data?.message
      if (detail) {
        ElMessage.error(detail)
      } else {
        ElMessage.error('启动研究任务失败，请稍后重试')
      }
    },
  }
)

const startResearch = () => {
  if (!researchForm.query.trim()) {
    ElMessage.warning('请输入研究主题')
    return
  }

  currentTaskId.value = null
  pollCount = 0
  currentPollInterval = BASE_POLL_INTERVAL
  elapsedSeconds.value = 0
  docAnalysisContent.value = null
  docAnalysisFile.value = null

  return runStartResearch()
}

const startElapsedTimer = () => {
  stopElapsedTimer()
  elapsedTimer = setInterval(() => {
    elapsedSeconds.value++
  }, 1000)
}

const stopElapsedTimer = () => {
  if (elapsedTimer) {
    clearInterval(elapsedTimer)
    elapsedTimer = null
  }
}

const connectSSE = async (taskId) => {
  closeSSE()

  sseAbortController = new AbortController()
  sseReaderActive = true

  try {
    const response = await deepResearchAPI.streamFetch(taskId, {
      signal: sseAbortController.signal,
    })

    if (!response.ok) {
      // rd-06：错误响应解析统一到 apiErrorHandler.extractSSEError
      throw await extractSSEError(response)
    }

    await readSSEStream(response, (data) => {
      if (!sseReaderActive) return
      handleSSEEvent(data)
    }, sseAbortController.signal)

    if (sseReaderActive && task.value && !isTerminalStatus(task.value.status)) {
      pollingTimer = setTimeout(pollTaskStatus, currentPollInterval)
    }
  } catch (error) {
    if (error.name === 'AbortError') {
      return
    }
    logger.error('SSE连接失败，回退到轮询:', error)
    if (task.value && !isTerminalStatus(task.value.status)) {
      pollingTimer = setTimeout(pollTaskStatus, currentPollInterval)
    }
  } finally {
    sseReaderActive = false
    sseAbortController = null
  }
}

const handleSSEEvent = (sseData) => {
  // 命名边界：readSSEStream 已统一调用 parseProtocolEvent 完成转换
  // （toCamelCase + type 还原为后端原始 snake_case 协议路由标识符），
  // 消费方只拿已转换对象，case 分支字段访问均为 camelCase。
  switch (sseData.type) {
    case 'connected':
      progressMessage.value = '已连接，等待研究启动...'
      break
    case 'status_change':
      if (currentTaskId.value) {
        const updateData = { status: sseData.status }
        if (sseData.finalReport) {
          updateData.finalReport = sseData.finalReport
        }
        researchStore.setTaskStatus(currentTaskId.value, updateData)
        progressMessage.value = sseData.message || ''
      }
      if (sseData.status === ResearchTaskStatus.COMPLETED || sseData.status === ResearchTaskStatus.FAILED) {
        closeSSE()
        stopElapsedTimer()
        // 主动获取完整任务数据，确保 final_report、files 等字段不丢失
        if (sseData.status === ResearchTaskStatus.COMPLETED && task.value?.taskId) {
          deepResearchAPI.getStatus(task.value.taskId).then(resp => {
            const fresh = resp.data?.data || resp.data
            if (fresh) {
              researchStore.setTaskStatus(currentTaskId.value, fresh)
            }
          }).catch(() => {})
        }
        checkDocAnalysisFile()
        autoLoadDocAnalysis()
      }
      break
    case 'step_update':
      progressMessage.value = sseData.step || ''
      break
    case 'done':
      closeSSE()
      stopElapsedTimer()
      // SSE 流结束但任务可能尚未完成（如连接超时），启动轮询检查
      if (task.value && !isTerminalStatus(task.value.status)) {
        pollingTimer = setTimeout(pollTaskStatus, currentPollInterval)
      }
      break
    case 'timeout':
      progressMessage.value = '连接超时，正在回退到轮询模式...'
      closeSSE()
      pollingTimer = setTimeout(pollTaskStatus, currentPollInterval)
      break
    case 'error':
      ElMessage.error(sseData.message || '研究执行出错')
      closeSSE()
      stopElapsedTimer()
      break
    // 实时 approval / approval_timeout / approval_processed 事件由 WebSocket 处理
    // （syncStore.handleRealtimeEvent，于 subscribeRealtimeForTask 中订阅 task 频道），
    // SSE 仅处理 approval_history（初始历史审批加载，SSE 专属）。
    case 'approval_history':
      // 优先使用 SSE 事件自带的 taskId，避免 task.value 竞态
      if (sseData.data) {
        const effectiveTaskId = sseData.taskId || task.value?.taskId
        if (effectiveTaskId) {
          approvalStore.restoreFromSSEHistory(sseData.data, effectiveTaskId)
        }
      }
      break
  }
}

const closeSSE = () => {
  sseReaderActive = false
  if (sseAbortController) {
    sseAbortController.abort()
    sseAbortController = null
  }
}

const { subscribeRealtimeForTask, clearRealtimeSubscriptions } = useTaskRealtimeSync('DeepResearch', 'taskId')

// 任务列表实时同步（user 频道 task_created/task_status_changed/task_deleted，公共能力）：
// 新任务出现、状态更新、删除均自动刷新；当前详情被其他浏览器删除时关闭详情视图
const taskListSync = useTaskListRealtimeSync('DeepResearch', () => taskListRef.value, {
  onTaskDeleted: (payload) => {
    const deletedTaskId = payload?.taskId
    if (deletedTaskId && currentTaskId.value === deletedTaskId) {
      deleteTask()
    }
  },
})

// viewTask 终态任务兜底刷新：拉取最新任务数据（fresh 可能补充 chat_session_id 等字段）
const {
  run: runRefreshTaskStatus,
} = useApiTask(
  (taskId) => deepResearchAPI.getStatus(taskId),
  {
    loading: false,
    showErrorToast: false,
    onSuccess: (resp) => {
      const fresh = resp.data?.data || resp.data
      if (fresh) {
        researchStore.setTaskStatus(currentTaskId.value, fresh)
        // fresh 可能补充 chat_session_id 字段，重新订阅（幂等：若已订阅同 task+session 则跳过）
        subscribeRealtimeForTask(task.value)
        if (fresh.status === ResearchTaskStatus.RUNNING || fresh.status === ResearchTaskStatus.PENDING) {
          startElapsedTimer()
          connectSSE(fresh.taskId)
        } else if (fresh.status === ResearchTaskStatus.COMPLETED) {
          autoLoadDocAnalysis()
        }
      }
    },
    onError: (e) => {
      logger.warn('获取任务最新状态失败:', e)
    },
  }
)

const viewTask = async (selectedTask) => {
  closeSSE()
  stopPolling()
  stopElapsedTimer()

  currentTaskId.value = selectedTask.taskId
  researchStore.setTaskStatus(currentTaskId.value, selectedTask, { force: true })
  showTaskDetail.value = true
  checkDocAnalysisFile()
  // 接入统一 WebSocket 实时同步：入口先订阅一次（基于 selectedTask 当前已知字段）
  subscribeRealtimeForTask(selectedTask)

  if (selectedTask.status === ResearchTaskStatus.RUNNING || selectedTask.status === ResearchTaskStatus.PENDING || selectedTask.status === ResearchTaskStatus.AWAITING_APPROVAL) {
    startElapsedTimer()
    connectSSE(selectedTask.taskId)
  } else if (selectedTask.taskId) {
    await runRefreshTaskStatus(selectedTask.taskId)
  }
}

const deleteTask = () => {
  // 停止轮询，避免对已删除任务持续请求
  stopPolling()
  // 断开 SSE 连接
  closeSSE()
  // 取消 WebSocket 实时订阅，避免对已删除任务继续接收事件
  clearRealtimeSubscriptions()
  // 清理该任务关联的审批条目
  if (task.value?.taskId) {
    approvalStore.clearByTaskId(task.value.taskId)
  }
  currentTaskId.value = null
  showTaskDetail.value = false
  docAnalysisContent.value = null
  docAnalysisFile.value = null
}

const onContinueModelChange = ({ providerId, modelName }) => {
  continueForm.providerId = providerId
  continueForm.modelName = modelName
}

const openContinueDialog = (taskData) => {
  continueParentTask.value = taskData
  continueForm.additionalQuery = ''
  continueForm.enableWebSearch = taskData.enableWebSearch ?? true
  continueForm.knowledgeBaseIds = taskData.knowledgeBaseIds || []
  continueForm.providerId = taskData.providerId || null
  continueForm.modelName = taskData.modelName || null
  continueForm.useMcp = taskData.useMcp ?? false
  continueForm.selectedMcpServers = taskData.selectedMcpServers || []
  continueForm.selectedTools = taskData.selectedTools || []
  if (continueForm.providerId && continueForm.modelName) {
    researchSettings.selectProvider(continueForm.providerId, continueForm.modelName)
  }
  continueDialogVisible.value = true
}

watch(continueDialogVisible, (visible) => {
  if (!visible && researchForm.providerId && researchForm.modelName) {
    researchSettings.selectProvider(researchForm.providerId, researchForm.modelName)
  }
})

const {
  run: runSubmitContinueResearch,
  loading: continueResearchLoading,
} = useApiTask(
  async () => {
    const modelConfig = researchSettings.getModelConfig()
    const response = await deepResearchAPI.continueResearch(
      continueParentTask.value.taskId,
      {
        additionalQuery: continueForm.additionalQuery,
        enableWebSearch: continueForm.enableWebSearch,
        enableDocAnalysis: continueForm.knowledgeBaseIds.length > 0,
        knowledgeBaseIds: continueForm.knowledgeBaseIds,
        useMcp: continueForm.useMcp,
        selectedMcpServers: continueForm.selectedMcpServers,
        selectedTools: continueForm.selectedTools,
        providerId: continueForm.providerId || modelConfig.providerId,
        modelName: continueForm.modelName || modelConfig.modelName,
        enableDeepThinking: researchSettings.thinkingEnabled,
        temperature: modelConfig.temperature,
        maxTokens: modelConfig.maxTokens,
        specialParams: modelConfig.specialParams,
      }
    )
    return response.data.data || response.data
  },
  {
    showErrorToast: false,
    onSuccess: (taskData) => {
      currentTaskId.value = taskData.taskId
      researchStore.setTaskStatus(currentTaskId.value, taskData, { force: true })
      showTaskDetail.value = true
      continueDialogVisible.value = false
      ElMessage.success('续研任务已启动')
      stopPolling()
      startElapsedTimer()
      // 续研任务同样作为新任务启动，立即订阅 WebSocket 实时事件
      subscribeRealtimeForTask(task.value)
      connectSSE(task.value.taskId)
    },
    onError: (error) => {
      logger.error('启动续研任务失败:', error)
      const detail = error.response?.data?.data || error.response?.data?.message
      ElMessage.error(detail || '启动续研任务失败')
    },
  }
)

const submitContinueResearch = () => {
  if (!continueParentTask.value) return
  return runSubmitContinueResearch()
}

// 开始研究 / 开始续研按钮共用 loading（原共用 ref，改为两个任务 loading 的合并投影）
const isLoading = computed(() => startResearchLoading.value || continueResearchLoading.value)

const handleContinueTask = (taskData) => {
  openContinueDialog(taskData)
}

const openInChat = () => {
  if (!task.value?.query || !task.value?.taskId) {
    ElMessage.warning('研究任务信息不完整，无法在聊天中讨论')
    return
  }
  const taskId = task.value.taskId
  const sessionId = task.value.sessionId
  const researchQuery = task.value.query
  // 如果研究任务关联了聊天会话，跳转到该会话；否则创建新会话
  const query = {
    research_task_id: taskId,
    q: `关于"${researchQuery}"的深度研究，请帮我进一步分析`,
    session_id: sessionId || undefined,
    research_query: researchQuery,
  }
  console.log('[DeepResearch] 跳转聊天:', {
    taskId: taskId,
    sessionId: sessionId || '(未关联)',
    hasSessionId: !!sessionId,
    researchQuery: researchQuery,
  })
  router.push({ path: '/chat', query })
}

const {
  run: runFileSearch,
  loading: fileSearchLoading,
} = useApiTask(
  (query) => deepResearchAPI.searchFiles(query),
  {
    showErrorToast: false,
    onSuccess: (response) => {
      if (response.data?.code === 200) {
        fileSearchResults.value = response.data.data?.items || response.data.data?.files || []
      }
    },
    onError: (error) => {
      logger.error('文件搜索失败:', error)
      ElMessage.error('文件搜索失败')
    },
  }
)

const handleFileSearch = () => {
  if (!fileSearchQuery.value.trim()) return
  fileSearchSearched.value = true
  return runFileSearch(fileSearchQuery.value)
}

// onActivated 自动选中 URL 指定任务（与当前任务不同时才加载；watch 路由变化复用同一 run）
const {
  run: runLoadTaskOnActivate,
} = useApiTask(
  (taskId) => deepResearchAPI.getStatus(taskId),
  {
    loading: false,
    showErrorToast: false,
    onSuccess: (resp) => {
      const taskData = resp.data?.data || resp.data
      if (taskData) {
        viewTask(taskData)
      }
    },
    onError: (e) => {
      logger.warn('[DeepResearchView] onActivated 加载任务失败:', e)
    },
  }
)

// onActivated 刷新已查看任务的最新状态（终态任务）
const {
  run: runRefreshTaskOnActivate,
} = useApiTask(
  (taskId) => deepResearchAPI.getStatus(taskId),
  {
    loading: false,
    showErrorToast: false,
    onSuccess: (resp) => {
      const fresh = resp.data?.data || resp.data
      if (fresh) {
        researchStore.setTaskStatus(currentTaskId.value, fresh)
        // fresh 可能补充 chat_session_id，重新订阅（幂等）
        subscribeRealtimeForTask(task.value)
        // 如果状态变为已完成，自动加载文档分析（文件列表由 FileBrowser 自管理自动刷新）
        if (fresh.status === ResearchTaskStatus.COMPLETED) {
          autoLoadDocAnalysis()
        }
      }
    },
    onError: (e) => {
      logger.warn('[DeepResearchView] onActivated 刷新任务状态失败:', e)
    },
  }
)

// keep-alive 首挂 mounted→activated 依次触发，初始化统一收敛 onActivated 单一入口
// （此前 onMounted/onActivated 双钩子同帧双触发 taskListSync.start 与 URL 任务加载，
// 任务列表与 getStatus 接口双发）；仅首挂需要的逻辑由首挂标志区分。
let isFirstActivate = true
onActivated(() => {
  // 订阅 user 频道任务事件（task_created/task_status_changed/task_deleted，公共能力），
  // 附带一次兜底刷新：任务列表首次加载可能在任务创建之前完成
  taskListSync.start()
  if (isFirstActivate) {
    isFirstActivate = false
    // 根因 C：确保独立设置 store 已从全局 modelStore 同步初始模型配置
    // （ModelSelector 挂载后 loadProviders 完成即同步；此处兜底一次）
    researchSettings.syncFromModelStore()
    refreshKnowledgeBases()
  }
  const taskId = getQueryParam(route, 'task_id')
  // 如果 URL 带有 task_id 且当前没有查看任务，自动加载（支持从聊天模块跳转）
  if (taskId && (!task.value || task.value.taskId !== taskId)) {
    runLoadTaskOnActivate(taskId)
    return
  }

  // 如果正在查看任务，根据状态决定是否重连/刷新
  if (task.value && task.value.taskId) {
    // 重新订阅 WebSocket（onDeactivated 时已清理，此处恢复）
    subscribeRealtimeForTask(task.value)
    if (task.value.status === ResearchTaskStatus.RUNNING || task.value.status === ResearchTaskStatus.PENDING) {
      // 任务还在运行，重连 SSE 或启动轮询
      startElapsedTimer()
      connectSSE(task.value.taskId)
    } else {
      // 任务已完成，刷新最新数据
      runRefreshTaskOnActivate(task.value.taskId)
    }
  }
})

// keep-alive 停用时：清理 SSE、轮询、WebSocket 订阅与列表事件监听，避免后台资源浪费
onDeactivated(() => {
  stopPolling()
  closeSSE()
  stopElapsedTimer()
  clearRealtimeSubscriptions()
  taskListSync.stop()
})

// 路由参数变化自动加载任务（支持从聊天页面多次跳转到不同任务），
// 复用 runLoadTaskOnActivate（taskFn 与回调完全等价，原独立定义已合并）
watch(() => getQueryParam(route, 'task_id'), (newTaskId) => {
  if (!newTaskId) return
  if (task.value && task.value.taskId === newTaskId) return
  runLoadTaskOnActivate(newTaskId)
})

// 审批清除后恢复轮询（所有待审批项处理完后，后台可能已完成但前端未刷新）
let _prevPendingSize = 0
watch(
  () => approvalStore.pendingApprovals.size,
  (newSize) => {
    if (_prevPendingSize > 0 && newSize === 0 && task.value?.status === ResearchTaskStatus.RUNNING) {
      logger.info('[DeepResearch] 审批已全部处理，恢复轮询')
      pollTaskStatus()
    }
    _prevPendingSize = newSize
  }
)

onUnmounted(() => {
  stopPolling()
  closeSSE()
  stopElapsedTimer()
  clearRealtimeSubscriptions()
  taskListSync.stop()
})
</script>

<style scoped>
.deep-research-view {
  height: 100%;
  padding: 24px;
  overflow-y: auto;
}

.view-content {
  max-width: 1200px;
  margin: 0 auto;
}

.page-title {
  font-size: 18px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.start-card {
  margin-bottom: 20px;
}

.task-detail-card,
.task-list-card {
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

.report-section {
  margin-top: 24px;
}

.report-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.report-header h4 {
  margin: 0;
}

.report-section h4 {
  margin: 0 0 16px 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.report-content {
  padding: 20px;
  background: var(--el-fill-color-lighter);
  border-radius: 4px;
}

.kb-selector {
  width: 100%;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 12px;
  background: var(--el-fill-color-lighter);
}

.kb-selector-header {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}

.kb-search-input {
  flex: 1;
}

.kb-loading,
.kb-empty {
  padding: 20px;
  text-align: center;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
}

.kb-list {
  max-height: 240px;
  overflow-y: auto;
}

.kb-item {
  padding: 8px 4px;
  border-bottom: 1px solid var(--el-border-color-extra-light);
}

.kb-item:last-child {
  border-bottom: none;
}

.kb-item-content {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.kb-name {
  font-weight: 500;
  font-size: 14px;
}

.kb-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.kb-desc {
  max-width: 300px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.kb-selected-summary {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--el-border-color-extra-light);
  font-size: 12px;
  color: var(--el-color-primary);
}

.kb-tag {
  margin-right: 6px;
  margin-bottom: 4px;
}

.tool-selector-wrapper {
  display: flex;
  align-items: center;
  gap: 12px;
}

.tool-selected-hint {
  font-size: 12px;
  color: var(--el-color-primary);
}

.deep-thinking-hint {
  margin-left: 12px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.analysis-section {
  margin-top: 8px;
}

.analysis-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.analysis-header h4 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.analysis-loading {
  padding: 20px;
  text-align: center;
  color: var(--el-text-color-secondary);
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
}

@media (max-width: 1024px) {
  .deep-research-view {
    padding: 16px;
  }

  .page-title {
    font-size: 16px;
  }
}

@media (max-width: 768px) {
  .deep-research-view {
    padding: 12px;
  }

  .page-title {
    font-size: 15px;
  }

  .kb-selector {
    padding: 8px;
  }

  .kb-list {
    max-height: 180px;
  }

  .report-content {
    padding: 12px;
  }
}

@media (max-width: 480px) {
  .deep-research-view {
    padding: 8px;
  }

  .page-title {
    font-size: 14px;
  }

  .card-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }

  .header-actions {
    flex-wrap: wrap;
    gap: 8px;
  }

  .kb-selector-header {
    flex-direction: column;
  }

  .kb-desc {
    max-width: 180px;
  }

  .report-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }
}
.analysis-content {
  padding: 16px;
  background: var(--el-fill-color-lighter);
  border-radius: 4px;
  border-left: 3px solid var(--el-color-primary);
}

.files-section {
  margin-top: 8px;
}

.files-section h4 {
  margin: 0 0 16px 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.file-search-section {
  margin-bottom: 16px;
  padding: 16px;
  background: var(--el-fill-color-lighter);
  border-radius: 8px;
}

.file-search-results {
  margin-top: 12px;
}

.version-chain {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.version-tag {
  cursor: default;
}

.version-arrow {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.report-header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

@media (max-width: 768px) {
  .deep-research-view {
    padding: 12px;
  }

  .card-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }

  .header-actions {
    width: 100%;
    flex-wrap: wrap;
  }

  .report-content {
    padding: 12px;
  }

  .progress-section {
    padding: 12px;
  }

  .kb-desc {
    max-width: 160px;
  }
}

@media (max-width: 480px) {
  .deep-research-view {
    padding: 8px;
  }

  .page-title {
    font-size: 16px;
  }
}

.approval-section {
  margin: 16px 0;
}
</style>
