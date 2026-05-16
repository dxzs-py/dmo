<script setup>
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { useChatStore } from '../stores/chat'
import { useSessionStore } from '../stores/session'
import { useModelStore } from '../stores/model'
import { chatAPI } from '../api'
import { ElMessage } from 'element-plus'
import ChatHeader from '../components/chat/ChatHeader.vue'
import ChatMessages from '../components/chat/ChatMessages.vue'
import ChatInput from '../components/chat/ChatInput.vue'
import ChatRightPanel from '../components/chat/ChatRightPanel.vue'
import ProjectContext from '../components/chat/ProjectContext.vue'
import ToolConfirmationDialog from '../components/chat/ToolConfirmationDialog.vue'
import { logger } from '../utils/logger'

const chatStore = useChatStore()
const sessionStore = useSessionStore()
const modelStore = useModelStore()

const inputMessage = ref('')
const useWebSearch = ref(false)
const useMcp = ref(false)
const useMicrophone = ref(false)
const selectedMcpServers = ref([])
const selectedTools = ref([])
const mcpTools = ref([])
const mcpServers = ref([])
const mcpAvailable = ref(false)
const isScrolled = ref(false)
const pendingAttachments = ref([])
const showRightPanel = ref(false)
const showDebug = ref(false)
const selectedMessage = ref(null)
const showToolConfirmation = ref(false)

const connectionStatus = computed(() => chatStore.connectionStatus)

const messages = computed(() => {
  return sessionStore.getSessionMessages(sessionStore.currentSessionId)
})

const hasMessages = computed(() => messages.value && messages.value.length > 0)

const handleScrollChange = (scrolled) => {
  isScrolled.value = scrolled
}

const sendMessage = async () => {
  if ((!inputMessage.value.trim() && pendingAttachments.value.length === 0) || chatStore.isLoading) {
    return
  }

  const message = inputMessage.value
  inputMessage.value = ''

  const attachmentsToUpload = [...pendingAttachments.value]
  pendingAttachments.value = []

  const uploadedAttachmentIds = []

  try {
    if (attachmentsToUpload.length > 0) {
      if (!sessionStore.currentSessionId) {
        await sessionStore.createNewSession(chatStore.currentMode)
      }

      for (const att of attachmentsToUpload) {
        try {
          const response = await chatAPI.uploadAttachment(sessionStore.currentSessionId, att.file)
          const data = response.data
          logger.log('[ChatView] 上传响应:', JSON.stringify(data))
          if (data?.data?.id) {
            uploadedAttachmentIds.push(data.data.id)
          }
        } catch (err) {
          logger.error('附件上传失败:', err)
          ElMessage.warning(`附件 ${att.name} 上传失败`)
        }
      }
    }

    logger.log('[ChatView] 发送消息, attachmentIds:', uploadedAttachmentIds)
    await chatStore.sendMessage(message, {
      useTools: true,
      useAdvancedTools: useWebSearch.value,
      useMcp: useMcp.value,
      selectedMcpServers: selectedMcpServers.value.length > 0 ? selectedMcpServers.value : null,
      selectedTools: selectedTools.value.length > 0 ? selectedTools.value : null,
      attachmentIds: uploadedAttachmentIds,
      ...modelStore.getModelConfig(),
    })
  } catch (error) {
    logger.error('发送消息失败:', error)
    ElMessage.error('发送消息失败，请稍后重试')
  }
}

const handleRegenerate = async (index) => {
  if (chatStore.isLoading) return

  try {
    await chatStore.regenerateMessage(index)
    ElMessage.success('正在重新生成回复...')
  } catch (error) {
    logger.error('重新生成失败:', error)
    ElMessage.error('重新生成失败，请稍后重试')
  }
}

const handleSuggestionClick = (suggestion) => {
  if (chatStore.isLoading) return
  inputMessage.value = suggestion
  sendMessage()
}

const handleAttach = async (files) => {
  for (const file of files) {
    if (file.size > 10 * 1024 * 1024) {
      ElMessage.warning(`文件 ${file.name} 超过10MB限制`)
      continue
    }
    pendingAttachments.value.push({
      name: file.name,
      size: file.size,
      fileType: file.name.split('.').pop().toLowerCase(),
      file: file,
    })
  }
}

const handleRemoveAttachment = (index) => {
  pendingAttachments.value.splice(index, 1)
}

const handleVoice = () => {
  ElMessage.info('语音输入功能正在开发中，敬请期待')
}

const handleWebSearchToggle = () => {
  useWebSearch.value = !useWebSearch.value
  ElMessage.success(useWebSearch.value ? '已开启网络搜索' : '已关闭网络搜索')
}

const fetchMcpTools = async () => {
  try {
    const res = await chatAPI.getMcpTools()
    const data = res.data?.data || res.data
    mcpAvailable.value = data.available || false
    mcpTools.value = data.tools || []
    mcpServers.value = data.servers || []
    if (mcpTools.value.length === 0 && !mcpAvailable.value) {
      ElMessage.warning('MCP 不可用：未安装 langchain-mcp-adapters 或未配置 MCP Server')
    } else if (mcpTools.value.length === 0) {
      ElMessage.info('MCP 已启用，但当前无可用工具（请检查 MCP Server 配置）')
    }
  } catch (e) {
    logger.warn('获取 MCP 工具列表失败:', e)
    mcpAvailable.value = false
  }
}

const handleMicrophoneToggle = () => {
  useMicrophone.value = !useMicrophone.value
  ElMessage.success(useMicrophone.value ? '已开启语音输入' : '已关闭语音输入')
}

const handleStopStreaming = () => {
  chatStore.stopStreaming()
}

const handleToggleRightPanel = () => {
  showRightPanel.value = !showRightPanel.value
  if (!showRightPanel.value) {
    selectedMessage.value = null
  }
}

const handleToggleDebug = () => {
  showDebug.value = !showDebug.value
  if (showDebug.value) {
    ElMessage.success('调试模式已开启 - 消息下方将显示调试信息')
  } else {
    ElMessage.info('调试模式已关闭')
  }
}

const handleMessageClick = (message) => {
  if (message.role === 'assistant') {
    selectedMessage.value = message
    if (!showRightPanel.value) {
      showRightPanel.value = true
    }
  }
}

const handleKeyDown = (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === 'k') {
    event.preventDefault()
    const inputEl = document.querySelector('.input-textarea')
    inputEl?.focus()
  }
  if ((event.ctrlKey || event.metaKey) && event.key === 'b') {
    event.preventDefault()
    handleToggleRightPanel()
  }
  if (event.key === 'Escape' && showRightPanel.value) {
    showRightPanel.value = false
    selectedMessage.value = null
  }
}

const loadCurrentSessionDetail = async () => {
  const sessionId = sessionStore.currentSessionId
  if (sessionId) {
    await sessionStore.loadSessionDetail(sessionId)
  }
}

onMounted(() => {
  chatStore.fetchModes()
  sessionStore.loadKnowledgeBases()
  loadCurrentSessionDetail()
  document.addEventListener('keydown', handleKeyDown)
})

onUnmounted(() => {
  document.removeEventListener('keydown', handleKeyDown)
})

watch(
  () => sessionStore.currentSessionId,
  (newSessionId) => {
    if (newSessionId) {
      selectedMessage.value = null
    }
  }
)

watch(() => messages.value?.length, (newLen) => {
  if (newLen > 0) {
    const lastMsg = messages.value[newLen - 1]
    if (lastMsg?.role === 'assistant' && (lastMsg.sources?.length || lastMsg.toolCalls?.length || lastMsg.reasoning)) {
      selectedMessage.value = lastMsg
    }
  }
})

watch(() => chatStore.pendingToolConfirmation, (val) => {
  if (val) {
    showToolConfirmation.value = true
  }
})

const handleToolApproved = (data) => {
  showToolConfirmation.value = false
  chatStore.pendingToolConfirmation = null
}

const handleToolDenied = () => {
  showToolConfirmation.value = false
  chatStore.pendingToolConfirmation = null
}
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
      @model-change="() => {}"
      @update:current-mode="(val) => chatStore.currentMode = val"
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
          />
        </Transition>

        <ChatInput
          v-model="inputMessage"
          :disabled="chatStore.isLoading"
          :loading="chatStore.isLoading"
          :is-streaming="chatStore.isStreaming"
          :use-web-search="useWebSearch"
          :use-mcp="useMcp"
          :selected-mcp-servers="selectedMcpServers"
          :selected-tools="selectedTools"
          :mcp-tools="mcpTools"
          :mcp-servers="mcpServers"
          :mcp-available="mcpAvailable"
          :use-microphone="useMicrophone"
          :attachments="pendingAttachments"
          @send="sendMessage"
          @attach="handleAttach"
          @remove-attachment="handleRemoveAttachment"
          @voice="handleVoice"
          @web-search="handleWebSearchToggle"
          @update:use-mcp="(val) => useMcp = val"
          @update:selected-mcp-servers="(val) => selectedMcpServers = val"
          @update:selected-tools="(val) => selectedTools = val"
          @microphone="handleMicrophoneToggle"
          @stop-streaming="handleStopStreaming"
        />
      </div>

      <ChatRightPanel
        :message="selectedMessage"
        :visible="showRightPanel"
        :cost-summary="chatStore.costSummary"
        :session-id="sessionStore.currentSessionId"
      />
    </div>

    <ToolConfirmationDialog
      v-model="showToolConfirmation"
      :confirm-id="chatStore.pendingToolConfirmation?.confirmId || ''"
      :tool-name="chatStore.pendingToolConfirmation?.toolName || ''"
      :tool-args="chatStore.pendingToolConfirmation?.toolArgs || {}"
      @approved="handleToolApproved"
      @denied="handleToolDenied"
    />
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
