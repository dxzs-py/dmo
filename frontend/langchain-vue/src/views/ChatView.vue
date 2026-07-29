<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { watchDebounced } from '@vueuse/core'
import { useRoute } from 'vue-router'
import { useChatStore } from '../stores/chat'
import { useSessionStore } from '../stores/session'
import { useModelStore } from '../stores/model'
import { useApprovalStore } from '../stores/approval'
import { useChatInput } from '../composables/useChatInput'
import { useChatUI } from '../composables/useChatUI'
import { useChatCommands } from '../composables/useChatCommands'
import { useChatKeyboard } from '../composables/useChatKeyboard'
import { deepResearchAPI } from '../api/research'
import ChatHeader from '../components/chat/ChatHeader.vue'
import ChatMessages from '../components/chat/ChatMessages.vue'
import ChatInput from '../components/chat/ChatInput.vue'
import ChatRightPanel from '../components/chat/ChatRightPanel.vue'
import ProjectContext from '../components/chat/ProjectContext.vue'

const chatStore = useChatStore()
const sessionStore = useSessionStore()
const modelStore = useModelStore()
const approvalStore = useApprovalStore()
const route = useRoute()

// --- 输入相关逻辑 ---
const {
  inputMessage,
  useWebSearch,
  useDeepThinking,
  selectedMcpServers,
  selectedTools,
  pendingAttachments,
  isUploading,
  sendMessage,
  handleSuggestionClick,
  handleAttach,
  handleRemoveAttachment,
  retryUpload,
  cancelUpload,
  handleWebSearchToggle,
  loadSessionAttachments,
  clearAttachments,
} = useChatInput()

// --- UI 状态逻辑 ---
const {
  showRightPanel,
  showDebug,
  selectedMessage,
  messages,
  hasMessages,
  handleScrollChange,
  handleToggleRightPanel,
  handleToggleDebug,
  handleMessageClick,
  handleRegenerate,
  handleDelete,
} = useChatUI()

// --- 命令处理逻辑 ---
const { handleCommandSelect } = useChatCommands({
  clearSelectedMessage: () => { selectedMessage.value = null },
})

// --- 键盘快捷键 ---
const chatInputRef = ref(null)

useChatKeyboard({
  onToggleRightPanel: handleToggleRightPanel,
  onEscape: () => {
    showRightPanel.value = false
    selectedMessage.value = null
  },
  inputRef: chatInputRef,
})

const connectionStatus = computed(() => chatStore.connectionStatus)

// --- 深度研究 SSE 重连（刷新/新浏览器恢复审批监听） ---
let researchSSEAbortController = null
let researchSSERetryCount = 0
const MAX_SSE_RETRY = 3
const SSE_RETRY_BASE_DELAY = 2000 // 2s, 4s, 8s

const connectResearchSSE = async (taskId) => {
  if (!taskId) return
  // 先检查任务状态，只有 running 才连接
  try {
    const resp = await deepResearchAPI.getStatus(taskId)
    const taskData = resp.data?.data || resp.data
    if (!taskData || taskData.status !== 'running') return
  } catch (error) {
    // 任务不存在（404）时清空 researchTaskId，避免后续重复请求已删除任务
    if (error?.response?.status === 404 && chatStore.researchTaskId === taskId) {
      chatStore.researchTaskId = null
    }
    return // 查询失败则不连接
  }

  researchSSEAbortController = new AbortController()
  try {
    const response = await deepResearchAPI.streamFetch(taskId, {
      signal: researchSSEAbortController.signal,
    })
    if (!response.ok) return

    // 连接成功，重置重连计数
    researchSSERetryCount = 0

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    const processChunk = async () => {
      try {
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split('\n')
          buffer = lines.pop() || ''
          for (const line of lines) {
            if (!line.startsWith('data: ') || line === 'data: [DONE]') continue
            try {
              const parsed = JSON.parse(line.slice(6))
              if (parsed.type === 'approval' || parsed.type === 'approval_timeout' || parsed.type === 'approval_processed') {
                approvalStore.handleApprovalEvent(parsed.data || parsed, {
                  source: 'deep_research',
                  taskId,
                  sessionId: sessionStore.currentSessionId,
                })
              } else if (parsed.type === 'approval_history') {
                const effectiveTaskId = parsed.task_id || taskId
                if (parsed.data) {
                  approvalStore.restoreFromSSEHistory(parsed.data, effectiveTaskId, sessionStore.currentSessionId)
                }
              } else if (parsed.type === 'status_change') {
                const status = parsed.status
                if (status === 'completed' || status === 'failed') {
                  researchSSEAbortController?.abort()
                  return
                }
              }
            } catch { /* 忽略 JSON 解析错误 */ }
          }
        }
      } catch (e) {
        if (e.name !== 'AbortError') {
          console.warn('[ChatView] 深度研究 SSE 连接异常:', e)
          // 指数退避重连
          if (researchSSERetryCount < MAX_SSE_RETRY) {
            researchSSERetryCount++
            const delay = SSE_RETRY_BASE_DELAY * Math.pow(2, researchSSERetryCount - 1)
            console.log(`[ChatView] ${delay}ms 后重连深度研究 SSE (第${researchSSERetryCount}次)`)
            setTimeout(() => connectResearchSSE(taskId), delay)
          }
        }
      } finally {
        reader.releaseLock()
      }
    }
    processChunk() // 不 await，后台运行
  } catch (e) {
    if (e.name !== 'AbortError') {
      console.warn('[ChatView] 深度研究 SSE 连接失败:', e)
      // 连接失败也尝试重连
      if (researchSSERetryCount < MAX_SSE_RETRY) {
        researchSSERetryCount++
        const delay = SSE_RETRY_BASE_DELAY * Math.pow(2, researchSSERetryCount - 1)
        console.log(`[ChatView] ${delay}ms 后重连深度研究 SSE (第${researchSSERetryCount}次)`)
        setTimeout(() => connectResearchSSE(taskId), delay)
      }
    }
  }
}

const disconnectResearchSSE = () => {
  if (researchSSEAbortController) {
    researchSSEAbortController.abort()
    researchSSEAbortController = null
  }
  researchSSERetryCount = 0
}

const handleModeChange = (newMode) => {
  chatStore.currentMode = newMode
}

const handleStopStreaming = () => {
  chatStore.stopStreaming()
}

const handleContinueResearch = (taskId) => {
  if (chatStore.isLoading) return
  chatStore.currentMode = 'deep-research'
  chatStore.sendMessage('请继续深入研究', {
    useTools: true,
    continue_task_id: taskId,
  })
}

const handleApprove = (payload) => {
  // payload 格式：
  // 1. 消息级审批（旧）: { message, approval, user_input? }
  // 2. ToolCall 级审批（新）: { message, approval, user_input? }
  // 3. 纯 message 对象（极旧兼容）
  const approval = payload?.approval || payload?.message?.approval
  if (!approval) return
  const userInput = payload?.user_input
  if (userInput !== undefined) {
    chatStore.approveCommand(approval, userInput)
  } else {
    chatStore.approveCommand(approval)
  }
}

const handleReject = (payload) => {
  const approval = payload?.approval || payload?.message?.approval
  if (!approval) return
  chatStore.rejectCommand(approval)
}

const loadCurrentSessionDetail = async () => {
  const sessionId = sessionStore.currentSessionId
  if (sessionId) {
    const session = sessionStore.sessions.find(s => s.id === sessionId)
    if (!session || !session.messages || session.messages.length === 0) {
      await sessionStore.loadSessionDetail(sessionId)
    }
    await loadSessionAttachments(sessionId)
    // 页面刷新后从已加载的 toolCalls 中恢复 pendingApprovals Map
    approvalStore.restoreFromSession(sessionId)
    // 页面刷新后从消息历史恢复 researchTaskId
    chatStore.restoreResearchContextFromMessages(sessionId)
  } else {
    clearAttachments()
  }
}

onMounted(async () => {
  chatStore.fetchModes()

  // 如果从深度研究页面跳转过来，且指定了 session_id，先切换到该会话
  const targetSessionId = route.query.session_id
  const researchTaskId = route.query.research_task_id
  const queryMessage = route.query.q
  if (targetSessionId || researchTaskId) {
    console.log('[ChatView] 深度研究跳转参数:', {
      session_id: targetSessionId || '(未传递)',
      research_task_id: researchTaskId || '(未传递)',
      q: queryMessage || '(未传递)',
      currentSessionId: sessionStore.currentSessionId || '(无)',
      sessionMatch: targetSessionId === sessionStore.currentSessionId,
    })
  }

  // 尝试切换到目标会话（容错：会话可能已被删除）
  if (targetSessionId && targetSessionId !== sessionStore.currentSessionId) {
    const sessionExists = sessionStore.sessions.some(s => s.id === targetSessionId)
    if (sessionExists) {
      console.log('[ChatView] 切换会话:', sessionStore.currentSessionId, '→', targetSessionId)
      sessionStore.currentSessionId = targetSessionId
    } else {
      // 会话已被删除，降级使用当前会话或新建会话
      console.log('[ChatView] 目标会话已不存在，降级到当前会话:', sessionStore.currentSessionId || '(新建)')
      if (!sessionStore.currentSessionId) {
        await sessionStore.createNewSession()
      }
    }
  } else if (targetSessionId && targetSessionId === sessionStore.currentSessionId) {
    console.log('[ChatView] session_id 已匹配当前会话，无需切换')
  }

  // loadKnowledgeBases 已在 main.js sessionStore.initialize() 中调用，此处不再重复
  await loadCurrentSessionDetail()

  if (researchTaskId) {
    chatStore.researchTaskId = researchTaskId
    // 设置持久化研究上下文标识，不随消息发送清空
    const researchQuery = route.query.research_query || ''
    chatStore.researchContextInfo = { taskId: researchTaskId, query: researchQuery }
  }
  // 非 URL 跳转（如页面刷新）时，restoreResearchContextFromMessages 已在 loadCurrentSessionDetail 中调用
  if (queryMessage) {
    await nextTick()
    inputMessage.value = queryMessage
  }

  // 深度研究 SSE 重连：如果当前会话有进行中的深度研究任务，自动建立 SSE 连接恢复审批监听
  if (chatStore.researchTaskId) {
    connectResearchSSE(chatStore.researchTaskId)
  }
})

onUnmounted(() => {
  disconnectResearchSSE()
})

// 会话切换防抖：快速切换时只执行最后一次，避免请求风暴
// （loadSessionAttachments + restoreFromSession + connectResearchSSE 各触发 HTTP/SSE 请求）
// 200ms 对用户无感知，但能有效抑制连续点击会话列表产生的 N 倍请求
watchDebounced(() => sessionStore.currentSessionId, async (newId, oldId) => {
  if (newId !== oldId) {
    // 切换会话时断开旧的深度研究 SSE 连接
    disconnectResearchSSE()
    if (newId) {
      await loadSessionAttachments(newId)
      // 切换会话时恢复该会话的待审批状态
      approvalStore.restoreFromSession(newId)
      // 如果新会话有进行中的深度研究任务，自动建立 SSE 连接
      chatStore.restoreResearchContextFromMessages(newId)
      if (chatStore.researchTaskId) {
        connectResearchSSE(chatStore.researchTaskId)
      }
    } else {
      clearAttachments()
    }
  }
}, { debounce: 200 })
</script>

<template>
  <div class="chat-view">
    <ChatHeader
      title="智能聊天"
      :current-mode="chatStore.currentMode"
      :available-modes="chatStore.availableModes"
      :show-right-panel="showRightPanel"
      :show-debug="showDebug"
      :connection-status="connectionStatus"
      :use-web-search="useWebSearch"
      :use-deep-thinking="useDeepThinking"
      @model-change="() => {}"
      @update:current-mode="handleModeChange"
      @update:use-web-search="(val) => useWebSearch = val"
      @update:use-deep-thinking="(val) => useDeepThinking = val"
      @toggle-right-panel="handleToggleRightPanel"
      @toggle-debug="handleToggleDebug"
    />

    <div class="chat-body">
      <div class="chat-main-area">
        <Transition name="slide-down">
          <div v-if="chatStore.lastStreamError" class="stream-error-bar">
            <span class="error-icon">⚠️</span>
            <span class="error-text">{{ chatStore.lastStreamError }}</span>
            <button class="error-dismiss" @click="chatStore.clearError()">✕</button>
          </div>
        </Transition>

        <Transition name="fade" mode="out-in">
          <div v-if="!hasMessages" key="welcome" class="chat-welcome">
            <div class="welcome-content animate-scale-in">
              <div class="welcome-icon-wrapper">
                <div class="welcome-icon-bg"></div>
                <div class="welcome-icon">✨</div>
              </div>
              <h2 class="welcome-title">欢迎使用智能助手</h2>
              <p class="welcome-desc">我可以帮你解答问题、分析代码、生成内容，还可以进行深度研究和工作流学习。</p>
              <div class="welcome-features">
                <div class="feature-item">
                  <span class="feature-icon">🧠</span>
                  <span class="feature-label">深度思考</span>
                </div>
                <div class="feature-item">
                  <span class="feature-icon">📚</span>
                  <span class="feature-label">知识检索</span>
                </div>
                <div class="feature-item">
                  <span class="feature-icon">🔬</span>
                  <span class="feature-label">深度研究</span>
                </div>
                <div class="feature-item">
                  <span class="feature-icon">🔄</span>
                  <span class="feature-label">工作流</span>
                </div>
              </div>
              <ProjectContext class="welcome-context" />
            </div>
          </div>
          <ChatMessages
            v-else
            key="messages"
            :messages="messages"
            :is-loading="chatStore.isLoading"
            :is-streaming="chatStore.isStreaming"
            :selected-message-id="selectedMessage?.id"
            :show-debug="showDebug"
            @regenerate="handleRegenerate"
            @suggestion-click="handleSuggestionClick"
            @scroll-change="handleScrollChange"
            @message-click="handleMessageClick"
            @message-delete="handleDelete"
            @continue-research="handleContinueResearch"
            @approve="handleApprove"
            @reject="handleReject"
          />
        </Transition>

        <ChatInput
          ref="chatInputRef"
          v-model="inputMessage"
          :disabled="chatStore.isLoading"
          :loading="chatStore.isLoading"
          :is-streaming="chatStore.isStreaming"
          :use-web-search="useWebSearch"
          :selected-mcp-servers="selectedMcpServers"
          :selected-tools="selectedTools"
          :attachments="pendingAttachments"
          :current-mode="chatStore.currentMode"
          :use-deep-thinking="useDeepThinking"
          :model-supports-deep-thinking="modelStore.currentModelCapabilities.includes('deep_thinking')"
          :is-uploading="isUploading"
          :research-context-info="chatStore.researchContextInfo"
          @send="sendMessage"
          @attach="handleAttach"
          @remove-attachment="handleRemoveAttachment"
          @web-search="handleWebSearchToggle"
          @update:selected-mcp-servers="(val) => selectedMcpServers = val"
          @update:selected-tools="(val) => selectedTools = val"
          @update:use-deep-thinking="(val) => useDeepThinking = val"
          @stop-streaming="handleStopStreaming"
          @command-select="handleCommandSelect"
          @retry-upload="retryUpload"
          @cancel-upload="cancelUpload"
          @dismiss-research-context="chatStore.clearResearchContext()"
        />
      </div>

      <ChatRightPanel
        :message="selectedMessage"
        :visible="showRightPanel"
        :session-id="sessionStore.currentSessionId"
      />
    </div>
  </div>
</template>

<style scoped>
.chat-view {
  height: 100%;
  display: flex;
  flex-direction: column;
  background-color: var(--background);
  position: relative;
  overflow: hidden;
}

.chat-welcome {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px 20px;
}

.welcome-content {
  max-width: 600px;
  text-align: center;
}

.welcome-icon-wrapper {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 80px;
  height: 80px;
  margin-bottom: 24px;
}

.welcome-icon-bg {
  position: absolute;
  inset: -8px;
  border-radius: 50%;
  background: var(--gradient-primary);
  opacity: 0.12;
  animation: pulse-bg 3s ease-in-out infinite;
}

@keyframes pulse-bg {
  0%, 100% { transform: scale(1); opacity: 0.12; }
  50% { transform: scale(1.15); opacity: 0.06; }
}

.welcome-icon {
  position: relative;
  font-size: 40px;
  line-height: 1;
}

.welcome-title {
  font-size: 28px;
  font-weight: 700;
  margin: 0 0 12px;
  color: var(--foreground);
  letter-spacing: -0.02em;
}

.welcome-desc {
  font-size: 15px;
  color: var(--muted-foreground);
  margin: 0 0 28px;
  line-height: 1.7;
}

.welcome-features {
  display: flex;
  justify-content: center;
  gap: 24px;
  margin-bottom: 32px;
}

.feature-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 12px 16px;
  border-radius: var(--radius-lg);
  background: var(--card);
  border: 1px solid var(--border);
  transition: all var(--transition-normal);
  cursor: default;
}

.feature-item:hover {
  border-color: var(--sidebar-primary);
  box-shadow: var(--shadow-primary);
  transform: translateY(-2px);
}

.feature-icon {
  font-size: 24px;
}

.feature-label {
  font-size: 12px;
  font-weight: 500;
  color: var(--muted-foreground);
}

.welcome-context {
  text-align: left;
  max-width: 480px;
  margin: 0 auto;
  background: var(--card);
  border-radius: var(--radius-lg);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-sm);
}

.chat-body {
  flex: 1;
  display: flex;
  overflow: hidden;
  min-height: 0;
}

.chat-main-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  overflow: hidden;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

.slide-down-enter-active,
.slide-down-leave-active {
  transition: all var(--transition-normal);
}

.slide-down-enter-from,
.slide-down-leave-to {
  opacity: 0;
  transform: translateY(-100%);
}

.stream-error-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  background: color-mix(in srgb, var(--destructive) 8%, transparent);
  border-bottom: 1px solid color-mix(in srgb, var(--destructive) 20%, transparent);
  font-size: 13px;
  color: var(--destructive);
  flex-shrink: 0;
}

.error-icon {
  flex-shrink: 0;
}

.error-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.error-dismiss {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--destructive);
  cursor: pointer;
  flex-shrink: 0;
  transition: background var(--transition-fast);
}

.error-dismiss:hover {
  background: color-mix(in srgb, var(--destructive) 10%, transparent);
}

@media (max-width: 1024px) {
  .welcome-content {
    max-width: 480px;
    padding: 0 16px;
  }

  .welcome-title {
    font-size: 24px;
  }

  .welcome-features {
    gap: 16px;
  }
}

@media (max-width: 768px) {
  .chat-body {
    flex-direction: column;
  }

  .chat-body :deep(.right-panel) {
    position: fixed;
    inset: 0;
    z-index: 100;
    width: 100%;
    max-width: 100%;
    border-left: none;
    box-shadow: var(--shadow-xl);
  }

  .chat-welcome {
    padding: 24px 16px;
  }

  .welcome-title {
    font-size: 20px;
  }

  .welcome-desc {
    font-size: 14px;
  }

  .welcome-features {
    flex-wrap: wrap;
    gap: 12px;
  }

  .feature-item {
    padding: 10px 14px;
  }
}

@media (max-width: 480px) {
  .chat-welcome {
    padding: 16px 12px;
  }

  .welcome-content {
    max-width: 100%;
  }

  .welcome-title {
    font-size: 18px;
  }

  .welcome-desc {
    font-size: 13px;
    margin-bottom: 20px;
  }

  .welcome-features {
    gap: 8px;
  }

  .feature-item {
    padding: 8px 12px;
  }

  .feature-icon {
    font-size: 20px;
  }

  .feature-label {
    font-size: 11px;
  }

  .welcome-context {
    border-radius: var(--radius);
  }
}
</style>
