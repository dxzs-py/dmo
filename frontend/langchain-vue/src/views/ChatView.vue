<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useChatStore } from '../stores/chat'
import { useSessionStore } from '../stores/session'
import { chatAPI } from '../api'
import { ElMessage } from 'element-plus'
import ChatHeader from '../components/chat/ChatHeader.vue'
import ChatMessages from '../components/chat/ChatMessages.vue'
import ChatInput from '../components/chat/ChatInput.vue'
import ChatRightPanel from '../components/chat/ChatRightPanel.vue'
import ProjectContext from '../components/chat/ProjectContext.vue'
import { logger } from '../utils/logger'


const chatStore = useChatStore()
const sessionStore = useSessionStore()

const inputMessage = ref('')
const selectedModel = ref('deepseek-chat')
const useWebSearch = ref(false)
const useMicrophone = ref(false)
const isScrolled = ref(false)
const pendingAttachments = ref([])
const showRightPanel = ref(false)
const showDebug = ref(false)
const selectedMessage = ref(null)

const messages = computed(() => {
  return sessionStore.getSessionMessages(sessionStore.currentSessionId)
})

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

  try {
    if (attachmentsToUpload.length > 0 && sessionStore.currentSessionId) {
      for (const att of attachmentsToUpload) {
        try {
          await chatAPI.uploadAttachment(sessionStore.currentSessionId, att.file)
        } catch (err) {
          logger.error('附件上传失败:', err)
          ElMessage.warning(`附件 ${att.name} 上传失败`)
        }
      }
    }

    await chatStore.sendMessage(message, {
      useTools: true,
      useAdvancedTools: useWebSearch.value,
    })
  } catch (error) {
    logger.error('发送消息失败:', error)
    ElMessage.error('发送消息失败，请稍后重试')
  }
}

const handleRegenerate = async (index) => {
  if (chatStore.isLoading) {
    return
  }

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
}

const handleMessageClick = (message) => {
  if (message.role === 'assistant') {
    selectedMessage.value = message
    if (!showRightPanel.value) {
      showRightPanel.value = true
    }
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
  loadCurrentSessionDetail()
})

watch(
  () => sessionStore.currentSessionId,
  (newSessionId) => {
    if (newSessionId) {
      // 这里不重复调用 loadSessionDetail，因为 session store 中的 watch 已经处理了
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
</script>

<template>
  <div class="chat-view">
    <ChatHeader
      v-model:selected-model="selectedModel"
      title="智能聊天"
      :current-mode="chatStore.currentMode"
      :available-modes="chatStore.availableModes"
      :show-right-panel="showRightPanel"
      :show-debug="showDebug"
      @update:current-mode="(val) => chatStore.currentMode = val"
      @toggle-right-panel="handleToggleRightPanel"
      @toggle-debug="handleToggleDebug"
    />

    <div class="chat-body">
      <div class="chat-main-area">
        <div v-if="!messages || messages.length === 0" class="chat-welcome">
          <div class="welcome-content">
            <h2>欢迎使用智能助手</h2>
            <p class="welcome-desc">我可以帮你解答问题、分析代码、生成内容，还可以进行深度研究和工作流学习。</p>
            <ProjectContext class="welcome-context" />
          </div>
        </div>
        <ChatMessages
          v-else
          :messages="messages"
          :is-loading="chatStore.isLoading"
          :is-streaming="chatStore.isStreaming"
          :selected-message-id="selectedMessage?.id"
          @regenerate="handleRegenerate"
          @suggestion-click="handleSuggestionClick"
          @scroll-change="handleScrollChange"
          @message-click="handleMessageClick"
        />

        <ChatInput
          v-model="inputMessage"
          :disabled="chatStore.isLoading"
          :loading="chatStore.isLoading"
          :is-streaming="chatStore.isStreaming"
          :use-web-search="useWebSearch"
          :use-microphone="useMicrophone"
          :attachments="pendingAttachments"
          @send="sendMessage"
          @attach="handleAttach"
          @remove-attachment="handleRemoveAttachment"
          @voice="handleVoice"
          @web-search="handleWebSearchToggle"
          @microphone="handleMicrophoneToggle"
          @stop-streaming="handleStopStreaming"
        />
      </div>

      <ChatRightPanel
        :message="selectedMessage"
        :visible="showRightPanel"
      />
    </div>
  </div>
</template>

<style scoped>
.chat-view {
  height: 100%;
  display: flex;
  flex-direction: column;
  background-color: var(--el-bg-color-page);
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

.welcome-content h2 {
  font-size: 24px;
  font-weight: 600;
  margin: 0 0 12px;
  color: var(--el-text-color-primary);
}

.welcome-desc {
  font-size: 14px;
  color: var(--el-text-color-secondary);
  margin: 0 0 24px;
  line-height: 1.6;
}

.welcome-context {
  text-align: left;
  max-width: 480px;
  margin: 0 auto;
  background: var(--el-bg-color);
  border-radius: 12px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.06);
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
    box-shadow: 0 -4px 16px rgba(0, 0, 0, 0.1);
  }
}
</style>
