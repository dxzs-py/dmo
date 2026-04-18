<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useChatStore } from '../stores/chat'
import { useSessionStore } from '../stores/session'
import { chatAPI } from '../api/client'
import { ElMessage } from 'element-plus'
import ChatHeader from '../components/chat/ChatHeader.vue'
import ChatMessages from '../components/chat/ChatMessages.vue'
import ChatInput from '../components/chat/ChatInput.vue'
import ChatRightPanel from '../components/chat/ChatRightPanel.vue'
import { useDebounce } from '../composables/useDebounce'

const chatStore = useChatStore()
const sessionStore = useSessionStore()

const inputMessage = ref('')
const selectedModel = ref('deepseek-chat')
const useWebSearch = ref(false)
const useMicrophone = ref(false)
const isScrolled = ref(false)
const isSending = ref(false)
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
  if ((!inputMessage.value.trim() && pendingAttachments.value.length === 0) || chatStore.isLoading || isSending.value) {
    return
  }

  isSending.value = true
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
          console.error('附件上传失败:', err)
          ElMessage.warning(`附件 ${att.name} 上传失败`)
        }
      }
    }

    await chatStore.sendMessage(message, {
      useTools: true,
      useAdvancedTools: useWebSearch.value,
    })
  } catch (error) {
    console.error('发送消息失败:', error)
    ElMessage.error('发送消息失败，请稍后重试')
  } finally {
    setTimeout(() => {
      isSending.value = false
    }, 500)
  }
}

const handleRegenerate = async (index) => {
  if (chatStore.isLoading || isSending.value) {
    return
  }

  try {
    await chatStore.regenerateMessage(index)
    ElMessage.success('正在重新生成回复...')
  } catch (error) {
    console.error('重新生成失败:', error)
    ElMessage.error('重新生成失败，请稍后重试')
  }
}

const handleSuggestionClick = (suggestion) => {
  if (chatStore.isLoading || isSending.value) return
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
      loadCurrentSessionDetail()
      selectedMessage.value = null
    }
  }
)

watch(messages, (newMessages) => {
  if (newMessages?.length > 0) {
    const lastMsg = newMessages[newMessages.length - 1]
    if (lastMsg.role === 'assistant' && (lastMsg.sources?.length || lastMsg.toolCalls?.length || lastMsg.reasoning)) {
      selectedMessage.value = lastMsg
    }
  }
}, { deep: true })
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
        <ChatMessages
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
