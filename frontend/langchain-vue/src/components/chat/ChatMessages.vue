<script setup>
import { ref, watch, nextTick, computed, onMounted, onUnmounted } from 'vue'
import { ChatDotRound, Loading } from '@element-plus/icons-vue'
import { RecycleScroller } from 'vue-virtual-scroller'
import ChatMessage from './ChatMessage.vue'
import MessageVersionSelector from './MessageVersionSelector.vue'
import { AiSuggestion, AiSuggestions } from '../ai-elements'
import { validateMessage } from '../../types/props'

/**
 * ChatMessages组件 - 聊天消息列表容器
 * @component
 * @description 渲染聊天消息列表，支持虚拟滚动、加载状态、建议问题等功能
 */
const props = defineProps({
  /**
   * 消息数组
   * @type {Array<import('../../types').ChatMessage>}
   */
  messages: {
    type: Array,
    default: () => [],
    validator: (value) => {
      if (!Array.isArray(value)) return false
      return value.every(msg => validateMessage(msg))
    },
  },
  /**
   * 是否正在加载
   * @type {boolean}
   */
  isLoading: {
    type: Boolean,
    default: false,
  },
  /**
   * 是否正在流式输出
   * @type {boolean}
   */
  isStreaming: {
    type: Boolean,
    default: false,
  },
  /**
   * 最大显示消息数量（性能优化）
   * @type {number}
   */
  maxVisibleMessages: {
    type: Number,
    default: 0,
    validator: (value) => value >= 0,
  },
  /**
   * 启用虚拟滚动（当消息超过此数量时自动启用）
   * @type {number}
   */
  virtualScrollThreshold: {
    type: Number,
    default: 20,
    validator: (value) => value >= 10,
  },
})

const emit = defineEmits({
  /**
   * 重新生成消息事件
   * @param {number} index - 消息索引
   */
  regenerate: (index) => typeof index === 'number' && index >= 0,
  /**
   * 点击建议问题事件
   * @param {string} suggestion - 建议文本
   */
  suggestionClick: (suggestion) => typeof suggestion === 'string',
  /**
   * 滚动状态变化事件
   * @param {boolean} isScrolled - 是否已滚动
   */
  scrollChange: (isScrolled) => typeof isScrolled === 'boolean',
})

const messagesContainer = ref(null)
const virtualScrollerRef = ref(null)
const isScrolled = ref(false)

/**
 * 是否启用虚拟滚动（基于消息数量动态决定）
 */
const shouldUseVirtualScroll = computed(() => {
  return props.messages.length > props.virtualScrollThreshold
})

/**
 * 预估消息项高度（用于虚拟滚动计算）
 */
const ITEM_SIZE = 200

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

watch(() => props.messages, () => {
  scrollToBottom()
}, { deep: true })

watch(() => props.isStreaming, (newVal) => {
  if (newVal) scrollToBottom()
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
    <div ref="messagesContainer" class="messages-container">
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
              variant="outline"
              @click="handleSuggestionClick"
            />
          </AiSuggestions>
        </div>
      </div>
      
      <div v-else class="messages-list" role="log" aria-live="polite">
        <!-- 虚拟滚动模式：消息数量超过阈值时启用 -->
        <RecycleScroller
          v-if="shouldUseVirtualScroll"
          ref="virtualScrollerRef"
          class="virtual-scroller"
          :items="messages"
          :item-size="ITEM_SIZE"
          key-field="id"
          :buffer="200"
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
                @regenerate="(idx) => emit('regenerate', idx)"
              />
              <MessageVersionSelector 
                :message="msg" 
                :message-index="msgIndex" 
              />
            </div>
          </template>
        </RecycleScroller>

        <!-- 普通模式：消息数量较少时使用 -->
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
  overscroll-behavior: contain;
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
