<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useChatStore } from '../stores/chat'
import { useSessionStore } from '../stores/session'
import { ElMessage } from 'element-plus'
import { Close } from '@element-plus/icons-vue'
import ChatHeader from '../components/chat/ChatHeader.vue'
import ChatMessages from '../components/chat/ChatMessages.vue'
import ChatInput from '../components/chat/ChatInput.vue'
import { useDebounce } from '../composables/useDebounce'
const chatStore = useChatStore()
const sessionStore = useSessionStore()
const inputMessage = ref('')
const selectedModel = ref('deepseek-chat')
const useWebSearch = ref(false)
const useMicrophone = ref(false)
const scrollContainerRef = ref(null)
const isScrolled = ref(false)
const isSending = ref(false)

const messages = computed(() => {
  return sessionStore.getSessionMessages(sessionStore.currentSessionId)
})

const handleScrollChange = (scrolled) => {
  isScrolled.value = scrolled
}

/**
 * 发送消息（带防抖保护，防止快速重复点击）
 */
const sendMessage = async () => {
  if (!inputMessage.value.trim() || chatStore.isLoading || isSending.value) {
    return
  }

  isSending.value = true
  const message = inputMessage.value
  inputMessage.value = ''

  try {
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

const handleKeyDown = (event) => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    sendMessage()
  }
}

/**
 * 建议问题点击（带防抖）
 */
const { debouncedFn: debouncedSuggestionClick } = useDebounce(async (suggestion) => {
  if (chatStore.isLoading || isSending.value) return
  
  try {
    await chatStore.sendMessage(suggestion, {
      useTools: true,
      useAdvancedTools: useWebSearch.value,
    })
  } catch (error) {
    console.error('发送建议消息失败:', error)
    ElMessage.error('发送消息失败，请稍后重试')
  }
}, 300)

const handleSuggestionClick = (suggestion) => {
  debouncedSuggestionClick(suggestion)
}

const handleAttach = () => {
  ElMessage.info('附件上传功能正在开发中，敬请期待')
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
    }
  }
)
</script>

<template>
  <div class="chat-view">
    <ChatHeader
      v-model:selected-model="selectedModel"
      title="智能聊天"
      :current-mode="chatStore.currentMode"
      :available-modes="chatStore.availableModes"
      @update:current-mode="(val) => chatStore.currentMode = val"
    />

    <ChatMessages
      ref="scrollContainerRef"
      :messages="messages"
      :is-loading="chatStore.isLoading"
      :is-streaming="chatStore.isStreaming"
      @regenerate="handleRegenerate"
      @suggestion-click="handleSuggestionClick"
      @scroll-change="handleScrollChange"
    />

    <div v-if="chatStore.isStreaming" class="streaming-indicator">
      <el-button type="warning" size="small" @click="handleStopStreaming">
        <el-icon><Close /></el-icon>
        停止生成
      </el-button>
    </div>

    <ChatInput
      v-model="inputMessage"
      :disabled="chatStore.isLoading"
      :loading="chatStore.isLoading"
      :use-web-search="useWebSearch"
      :use-microphone="useMicrophone"
      @send="sendMessage"
      @keydown="handleKeyDown"
      @attach="handleAttach"
      @voice="handleVoice"
      @web-search="handleWebSearchToggle"
      @microphone="handleMicrophoneToggle"
    />
  </div>
</template>

<style scoped>
.chat-view {
  height: 100%;
  display: flex;
  flex-direction: column;
  background-color: var(--el-bg-color-page);
  position: relative;
}

.streaming-indicator {
  position: absolute;
  bottom: 100px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 10;
}
</style>
