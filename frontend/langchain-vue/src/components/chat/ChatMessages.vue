<script setup>
import { ref, watch, nextTick, computed, onMounted, onUnmounted } from 'vue'
import { ChatDotRound, Loading } from '@element-plus/icons-vue'
import ChatMessage from './ChatMessage.vue'
import MessageVersionSelector from './MessageVersionSelector.vue'
import { AiSuggestion, AiSuggestions } from '../ai-elements'

const props = defineProps({
  messages: {
    type: Array,
    default: () => [],
  },
  isLoading: {
    type: Boolean,
    default: false,
  },
  isStreaming: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits(['regenerate', 'suggestionClick', 'scrollChange'])

const messagesContainer = ref(null)
const isScrolled = ref(false)

const scrollToBottom = () => {
  nextTick(() => {
    if (messagesContainer.value) {
      messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight
    }
  })
}

const handleScroll = () => {
  if (!messagesContainer.value) return
  const isCurrentlyScrolled = messagesContainer.value.scrollTop > 0
  if (isCurrentlyScrolled !== isScrolled.value) {
    isScrolled.value = isCurrentlyScrolled
    emit('scrollChange', isScrolled.value)
  }
}

onMounted(() => {
  if (messagesContainer.value) {
    messagesContainer.value.addEventListener('scroll', handleScroll)
  }
})

onUnmounted(() => {
  if (messagesContainer.value) {
    messagesContainer.value.removeEventListener('scroll', handleScroll)
  }
})

watch(() => props.messages, () => {
  scrollToBottom()
}, { deep: true })

watch(() => props.isStreaming, (newVal) => {
  if (newVal) {
    scrollToBottom()
  }
})

const showLoadingIndicator = computed(() => {
  return props.isLoading && (props.messages.length === 0 || props.messages[props.messages.length - 1].role === 'user')
})

const suggestions = [
  "什么是人工智能？",
  "如何学习编程？",
  "解释机器学习的基本原理",
  "什么是深度学习？",
  "如何提高英语水平？",
  "推荐一些好书",
  "如何保持健康的生活方式？",
  "什么是区块链技术？"
]

const handleSuggestionClick = (suggestion) => {
  emit('suggestionClick', suggestion)
}
</script>

<template>
  <div class="chat-main">
    <div class="messages-container" ref="messagesContainer">
      <div v-if="messages.length === 0" class="empty-state">
        <div class="empty-icon">
          <ChatDotRound :size="64" />
        </div>
        <h3>您今天在想什么？</h3>
        <p>开始一个新的对话，我会尽力帮助您</p>
        
        <div class="suggestions-section">
          <h4 class="suggestions-title">推荐问题</h4>
          <AiSuggestions>
            <AiSuggestion 
              v-for="(suggestion, index) in suggestions" 
              :key="index"
              :suggestion="suggestion"
              @click="handleSuggestionClick"
              variant="outline"
            />
          </AiSuggestions>
        </div>
      </div>
      
      <div v-else class="messages-list">
        <TransitionGroup name="slide-up">
          <div v-for="(msg, msgIndex) in messages" :key="msg.id || msgIndex" class="message-wrapper">
            <ChatMessage
              :message="msg"
              :index="msgIndex"
              :is-last="msgIndex === messages.length - 1"
              :is-loading="isLoading"
              :is-streaming="isStreaming"
              @regenerate="(idx) => emit('regenerate', idx)"
            />
            <MessageVersionSelector 
              :message="msg" 
              :message-index="msgIndex" 
            />
          </div>
        </TransitionGroup>
        
        <div v-if="showLoadingIndicator" class="message assistant">
          <div class="message-avatar">
            <el-icon><ChatDotRound /></el-icon>
          </div>
          <div class="message-content">
            <div class="message-role">AI助手</div>
            <div class="message-text">
              <el-icon class="is-loading"><Loading /></el-icon> 正在思考...
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat-main {
  flex: 1;
  overflow: hidden;
  background-color: var(--el-fill-color-light);
}

.messages-container {
  height: 100%;
  overflow-y: auto;
  padding: 24px;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--el-text-color-secondary);
}

.empty-state .empty-icon {
  color: var(--el-fill-color);
  margin-bottom: 20px;
}

.empty-state h3 {
  font-size: 24px;
  color: var(--el-text-color-primary);
  margin-bottom: 8px;
}

.empty-state p {
  font-size: 14px;
}

.messages-list {
  max-width: 900px;
  margin: 0 auto;
}

.message-wrapper {
  margin-bottom: 24px;
}

.message {
  display: flex;
  margin-bottom: 16px;
  max-width: 100%;
}

.message.assistant {
  flex-direction: row;
}

.message-avatar {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  background: linear-gradient(135deg, var(--el-color-primary) 0%, var(--el-color-primary-light-3) 100%);
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  flex-shrink: 0;
  margin: 0 12px;
}

.message-content {
  flex: 1;
  min-width: 0;
}

.message-role {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  padding: 0 4px;
}

.message-text {
  background: var(--el-bg-color);
  border-radius: 12px;
  padding: 14px 18px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
  max-width: 100%;
  word-break: break-word;
}

.suggestions-section {
  margin-top: 24px;
}

.suggestions-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--el-text-color-primary);
  margin-bottom: 12px;
}
</style>
