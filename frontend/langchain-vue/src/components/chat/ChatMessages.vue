<script setup>
import { ref, watch, nextTick, computed, onMounted, onUnmounted } from 'vue'
import { ChatDotRound } from '@element-plus/icons-vue'
import { RecycleScroller } from 'vue-virtual-scroller'
import ChatMessage from './ChatMessage.vue'
import { AiSuggestion, AiSuggestions, AiShimmer } from '../ai-elements'
import AiLoader from '../ai-elements/AiLoader.vue'
import { validateMessage } from '../../types'

const props = defineProps({
  messages: {
    type: Array,
    default: () => [],
    validator: (value) => {
      if (!Array.isArray(value)) return false
      return value.every(msg => validateMessage(msg))
    },
  },
  isLoading: {
    type: Boolean,
    default: false,
  },
  isStreaming: {
    type: Boolean,
    default: false,
  },
  maxVisibleMessages: {
    type: Number,
    default: 0,
    validator: (value) => value >= 0,
  },
  virtualScrollThreshold: {
    type: Number,
    default: 20,
    validator: (value) => value >= 10,
  },
  selectedMessageId: {
    type: String,
    default: null,
  },
})

const emit = defineEmits({
  regenerate: (index) => typeof index === 'number' && index >= 0,
  suggestionClick: (suggestion) => typeof suggestion === 'string',
  scrollChange: (isScrolled) => typeof isScrolled === 'boolean',
  messageClick: (message) => message && typeof message === 'object',
})

const messagesContainer = ref(null)
const virtualScrollerRef = ref(null)
const isScrolled = ref(false)

const shouldUseVirtualScroll = computed(() => {
  return props.messages.length > props.virtualScrollThreshold
})

const estimateItemSize = (msgIndex) => {
  const msg = props.messages[msgIndex]
  if (!msg) return 200
  const contentLen = (msg.content || '').length
  if (contentLen < 50) return 120
  if (contentLen < 200) return 180
  if (contentLen < 500) return 280
  if (contentLen < 1000) return 400
  return 500
}

const scrollToBottom = (behavior = 'smooth') => {
  nextTick(() => {
    if (shouldUseVirtualScroll.value && virtualScrollerRef.value?.$el) {
      const el = virtualScrollerRef.value.$el
      el.scrollTo({
        top: el.scrollHeight,
        behavior: props.messages.length > 50 ? 'instant' : behavior,
      })
    } else if (messagesContainer.value) {
      messagesContainer.value.scrollTo({
        top: messagesContainer.value.scrollHeight,
        behavior: props.messages.length > 50 ? 'instant' : behavior,
      })
    }
  })
}

const handleScroll = () => {
  const container = shouldUseVirtualScroll.value 
    ? virtualScrollerRef.value?.$el 
    : messagesContainer.value
  
  if (!container) return
  const isCurrentlyScrolled = container.scrollTop > 0
  if (isCurrentlyScrolled !== isScrolled.value) {
    isScrolled.value = isCurrentlyScrolled
    emit('scrollChange', isScrolled.value)
  }
}

onMounted(() => {
  const container = messagesContainer.value
  if (container) {
    container.addEventListener('scroll', handleScroll, { passive: true })
  }
})

onUnmounted(() => {
  const container = messagesContainer.value
  if (container) {
    container.removeEventListener('scroll', handleScroll)
  }
})

watch(() => props.messages?.length, () => {
  scrollToBottom()
})

watch(() => props.isStreaming, (newVal) => {
  if (newVal) scrollToBottom()
})

const showLoadingIndicator = computed(() => {
  return props.isLoading && (props.messages.length === 0 || props.messages[props.messages.length - 1].role === 'user')
})

const lastAssistantMessage = computed(() => {
  for (let i = props.messages.length - 1; i >= 0; i--) {
    if (props.messages[i].role === 'assistant') return props.messages[i]
  }
  return null
})

const dynamicSuggestions = computed(() => {
  const last = lastAssistantMessage.value
  if (last?.suggestions && Array.isArray(last.suggestions) && last.suggestions.length > 0) {
    return last.suggestions
  }
  return []
})

const defaultSuggestions = [
  { text: "什么是人工智能？", icon: "🤖" },
  { text: "如何学习编程？", icon: "💻" },
  { text: "解释机器学习的基本原理", icon: "🧠" },
  { text: "什么是深度学习？", icon: "🔬" },
  { text: "如何提高英语水平？", icon: "📚" },
  { text: "推荐一些好书", icon: "📖" },
  { text: "如何保持健康的生活方式？", icon: "🏃" },
  { text: "什么是区块链技术？", icon: "⛓️" },
]

const displaySuggestions = computed(() => {
  if (dynamicSuggestions.value.length > 0) {
    return dynamicSuggestions.value.map(s => typeof s === 'string' ? { text: s, icon: '💡' } : s)
  }
  return defaultSuggestions
})

const handleSuggestionClick = (suggestion) => {
  const text = typeof suggestion === 'string' ? suggestion : suggestion.text
  emit('suggestionClick', text)
}
</script>

<template>
  <div class="chat-main">
    <div ref="messagesContainer" class="messages-container">
      <div v-if="messages.length === 0" class="empty-state">
        <div class="empty-icon-wrapper">
          <div class="empty-icon-bg"></div>
          <el-icon class="empty-icon" :size="48"><ChatDotRound /></el-icon>
        </div>
        <h3 class="empty-title">您好，有什么可以帮您？</h3>
        <p class="empty-desc">我是您的智能学习助手，可以回答问题、辅助学习、深度研究</p>
        
        <div class="suggestions-section">
          <h4 class="suggestions-title">试试以下问题</h4>
          <div class="suggestions-grid">
            <button
              v-for="(suggestion, index) in displaySuggestions"
              :key="index"
              class="suggestion-card"
              @click="handleSuggestionClick(suggestion)"
            >
              <span class="suggestion-icon">{{ suggestion.icon }}</span>
              <span class="suggestion-text">{{ suggestion.text }}</span>
            </button>
          </div>
        </div>
      </div>
      
      <div v-else class="messages-list" role="log" aria-live="polite">
        <RecycleScroller
          v-if="shouldUseVirtualScroll"
          ref="virtualScrollerRef"
          class="virtual-scroller"
          :items="messages"
          :item-size="200"
          size-field="estimatedSize"
          key-field="id"
          :buffer="400"
          @scroll="handleScroll"
        >
          <template #default="{ item: msg, index: msgIndex }">
            <div class="message-wrapper">
              <ChatMessage
                :message="msg"
                :index="msgIndex"
                :is-last="msgIndex === messages.length - 1"
                :is-loading="isLoading"
                :is-streaming="isStreaming"
                :is-selected="selectedMessageId && msg.id === selectedMessageId"
                @regenerate="(idx) => emit('regenerate', idx)"
                @click="(message) => emit('messageClick', message)"
            />
          </div>
          </template>
        </RecycleScroller>

        <TransitionGroup v-else name="slide-up" tag="div" class="messages-list-inner">
          <div 
            v-for="(msg, msgIndex) in messages" 
            :key="msg.id || msgIndex" 
            class="message-wrapper"
            style="content-visibility: auto; contain-intrinsic-size: auto 200px;"
          >
            <ChatMessage
              :message="msg"
              :index="msgIndex"
              :is-last="msgIndex === messages.length - 1"
              :is-loading="isLoading"
              :is-streaming="isStreaming"
              :is-selected="selectedMessageId && msg.id === selectedMessageId"
              @regenerate="(idx) => emit('regenerate', idx)"
              @click="(message) => emit('messageClick', message)"
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
              <AiLoader type="dots" size="small" text="AI 正在思考..." />
              <AiShimmer class="loading-shimmer" />
            </div>
          </div>
        </div>

        <div v-if="isStreaming && !showLoadingIndicator" class="ai-typing-indicator">
          <div class="typing-dots">
            <span class="typing-dot"></span>
            <span class="typing-dot"></span>
            <span class="typing-dot"></span>
          </div>
          <span class="typing-text">AI正在输入...</span>
        </div>

        <div v-if="dynamicSuggestions.length > 0 && !isStreaming" class="dynamic-suggestions">
          <div class="suggestions-grid compact">
            <button
              v-for="(suggestion, index) in displaySuggestions"
              :key="index"
              class="suggestion-card small"
              @click="handleSuggestionClick(suggestion)"
            >
              <span class="suggestion-icon">{{ suggestion.icon }}</span>
              <span class="suggestion-text">{{ suggestion.text }}</span>
            </button>
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
  overscroll-behavior: contain;
  padding: 24px;
  scrollbar-width: thin;
  scrollbar-color: var(--el-border-color) transparent;
}

.messages-container::-webkit-scrollbar {
  width: 6px;
}

.messages-container::-webkit-scrollbar-track {
  background: transparent;
}

.messages-container::-webkit-scrollbar-thumb {
  background-color: var(--el-border-color);
  border-radius: 3px;
}

.messages-container::-webkit-scrollbar-thumb:hover {
  background-color: var(--el-border-color-darker);
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--el-text-color-secondary);
  padding: 40px 20px;
}

.empty-icon-wrapper {
  position: relative;
  margin-bottom: 24px;
}

.empty-icon-bg {
  position: absolute;
  inset: -12px;
  border-radius: 50%;
  background: linear-gradient(135deg, var(--el-color-primary-light-8), var(--el-color-primary-light-9));
  animation: pulse-bg 3s ease-in-out infinite;
}

@keyframes pulse-bg {
  0%, 100% { transform: scale(1); opacity: 0.6; }
  50% { transform: scale(1.1); opacity: 0.3; }
}

.empty-icon {
  position: relative;
  color: var(--el-color-primary);
}

.empty-title {
  font-size: 24px;
  color: var(--el-text-color-primary);
  margin-bottom: 8px;
  font-weight: 600;
}

.empty-desc {
  font-size: 14px;
  color: var(--el-text-color-secondary);
  margin-bottom: 0;
}

.empty-state p {
  font-size: 14px;
}

.suggestions-section {
  margin-top: 32px;
  width: 100%;
  max-width: 640px;
}

.suggestions-title {
  font-size: 14px;
  font-weight: 500;
  color: var(--el-text-color-secondary);
  margin-bottom: 16px;
  text-align: center;
}

.suggestions-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px;
}

.suggestions-grid.compact {
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
}

.suggestion-card {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  border-radius: 12px;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  cursor: pointer;
  transition: all 0.2s ease;
  text-align: left;
  font-size: 13px;
  color: var(--el-text-color-regular);
  line-height: 1.4;
}

.suggestion-card:hover {
  border-color: var(--el-color-primary-light-5);
  background: var(--el-color-primary-light-9);
  transform: translateY(-1px);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
}

.suggestion-card:active {
  transform: translateY(0);
}

.suggestion-card.small {
  padding: 8px 12px;
  border-radius: 8px;
}

.suggestion-icon {
  font-size: 18px;
  flex-shrink: 0;
}

.suggestion-text {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.messages-list {
  max-width: 768px;
  margin: 0 auto;
}

.messages-list-inner {
  display: flex;
  flex-direction: column;
}

.virtual-scroller {
  height: 100%;
  overflow-y: auto;
}

.message-wrapper {
  margin-bottom: 24px;
  will-change: transform;
  padding: 12px 16px;
  border-bottom: 1px solid var(--el-border-color-lighter);
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

.loading-shimmer {
  width: 120px;
  height: 20px;
  border-radius: 4px;
}

.ai-typing-indicator {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  max-width: 768px;
  margin: 0 auto;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.typing-dots {
  display: flex;
  gap: 4px;
}

.typing-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background-color: var(--el-color-primary);
  animation: typing-bounce 1.4s infinite ease-in-out both;
}

.typing-dot:nth-child(1) { animation-delay: -0.32s; }
.typing-dot:nth-child(2) { animation-delay: -0.16s; }

@keyframes typing-bounce {
  0%, 80%, 100% { transform: scale(0); }
  40% { transform: scale(1); }
}

.typing-text {
  color: var(--el-text-color-placeholder);
}

.dynamic-suggestions {
  max-width: 768px;
  margin: 0 auto;
  padding: 8px 16px 16px;
}

@media (max-width: 768px) {
  .messages-container {
    padding: 16px 12px;
  }

  .suggestions-grid {
    grid-template-columns: 1fr;
  }

  .suggestion-card {
    padding: 10px 14px;
  }

  .message-wrapper {
    padding: 8px 8px;
  }
}
</style>
