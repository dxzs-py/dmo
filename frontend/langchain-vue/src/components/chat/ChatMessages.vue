<script setup>
import { ref, watch, nextTick, computed, onMounted, onUnmounted } from 'vue'
import { ChatDotRound, ArrowDown } from '@element-plus/icons-vue'
import { DynamicScroller, DynamicScrollerItem } from 'vue-virtual-scroller'
import 'vue-virtual-scroller/dist/vue-virtual-scroller.css'
import ChatMessage from './ChatMessage.vue'
import AiShimmer from '../ai-elements/AiShimmer.vue'
import AiLoader from '../ai-elements/AiLoader.vue'
import { useAutoFollowScroll, getDistanceFromBottom } from '@/composables/useAutoFollowScroll'
import { validateMessage } from '@/utils/validation'

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
  showDebug: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits({
  regenerate: (index) => typeof index === 'number' && index >= 0,
  'suggestion-click': (suggestion) => typeof suggestion === 'string',
  'scroll-change': (isScrolled) => typeof isScrolled === 'boolean',
  'message-click': (message) => message && typeof message === 'object',
  'message-delete': (payload) => payload && typeof payload.messageId !== 'undefined',
  'continue-research': (taskId) => typeof taskId === 'string' && taskId.length > 0,
  approve: (payload) => payload && typeof payload === 'object',
  reject: (message) => message && typeof message === 'object',
})

const messagesContainer = ref(null)
const virtualScrollerRef = ref(null)
const isScrolled = ref(false)

// 自动跟随滚动状态机（audit UX-05）：距底阈值内才自动滚底，用户上翻超出阈值后
// 暂停自动滚动并展示"回到底部"浮标，手动滚回底部附近自动恢复跟随
const {
  showNewContentBadge,
  newContentBadgeText,
  applyScrollPosition,
  forceFollow,
  handleNewMessages,
  handleStreamContentGrowth,
  handleMessagesRemoved,
} = useAutoFollowScroll()

const shouldUseVirtualScroll = computed(() => {
  return props.messages.length > props.virtualScrollThreshold
})

/** 当前滚动容器：虚拟滚动取 DynamicScroller 根元素，普通模式取外层容器 */
const getScrollContainer = () => {
  return shouldUseVirtualScroll.value ? virtualScrollerRef.value?.$el : messagesContainer.value
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
  const container = getScrollContainer()
  if (!container) return
  // 距底阈值内恢复自动跟随，超出阈值暂停（用户上翻阅读时不被强拉回底部）
  applyScrollPosition(getDistanceFromBottom(container))
  const isCurrentlyScrolled = container.scrollTop > 0
  if (isCurrentlyScrolled !== isScrolled.value) {
    isScrolled.value = isCurrentlyScrolled
    emit('scroll-change', isScrolled.value)
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

watch(() => props.messages?.length, (newLen, oldLen) => {
  if (typeof newLen !== 'number') return
  const prevLen = typeof oldLen === 'number' ? oldLen : 0
  if (newLen > prevLen) {
    const lastMessage = props.messages[newLen - 1]
    // 用户主动发送的新消息：无条件锚底并恢复跟随（用户发完消息预期立刻看到回复区）
    if (lastMessage?.role === 'user') {
      forceFollow()
      scrollToBottom()
      return
    }
    // 其他新消息（assistant 占位 / 会话同步）：跟随中滚底，暂停中累计未读计数（浮标）
    if (handleNewMessages(newLen - prevLen)) {
      scrollToBottom()
    }
  } else if (newLen < prevLen) {
    // 消息删除/回滚：未读计数失效清零，跟随/暂停状态本身不变
    handleMessagesRemoved()
  }
})

watch(() => props.isStreaming, (newVal) => {
  // 流式开始属于用户主动行为（发送/重新生成）：强制锚底并恢复跟随
  if (newVal) {
    forceFollow()
    scrollToBottom()
  }
})

/**
 * 流式产出跟随（audit UX-05）：末条 assistant 消息任一产出通道增长时
 * （正文/推理/工具调用/子代理卡），跟随中即时锚底；暂停中仅标记"有新内容"
 * （浮标提示），不拉动视口。签名取各通道规模之和，仅用于感知增长。
 */
watch(() => {
  const last = props.messages?.[props.messages.length - 1]
  if (!last || last.role !== 'assistant') return 0
  return (last.content?.length || 0)
    + (last.reasoning?.content?.length || 0)
    + (last.toolCalls?.length || 0)
    + (last.subagents?.length || 0)
}, (newSig, oldSig) => {
  if (!props.isStreaming || newSig === oldSig) return
  if (handleStreamContentGrowth()) {
    // 逐 chunk 跟随用 instant：smooth 动画在高频 chunk 下互相打断造成抖动
    scrollToBottom('instant')
  }
})

/** 点击"回到底部"浮标：恢复自动跟随并滚底 */
const handleScrollToBottomClick = () => {
  forceFollow()
  scrollToBottom()
}

const showLoadingIndicator = computed(() => {
  return props.isLoading && (props.messages.length === 0 || props.messages[props.messages.length - 1].role === 'user')
})

/**
 * 首内容判定（audit UX-06）：末条 assistant 消息任一输出通道已启动
 * （正文/推理/工具调用/计划）。选用"多通道或"而非仅判 content：
 * 推理流与工具调用先于正文产出时，仅看 content 会把已开始的产出
 * 误报为"正在准备回复"。工具执行过程本身由消息内 ToolCallCard 独立呈现
 * （audit 维度 1 无缺陷），此处仅区分"准备回复"与"正在输入"两个阶段。
 */
const hasStreamOutput = computed(() => {
  const last = props.messages?.[props.messages.length - 1]
  if (!last || last.role !== 'assistant') return false
  return (last.content || '').length > 0
    || (last.reasoning?.content || '').length > 0
    || (Array.isArray(last.toolCalls) && last.toolCalls.length > 0)
    || Boolean(last.plan)
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
  emit('suggestion-click', text)
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
        <DynamicScroller
          v-if="shouldUseVirtualScroll"
          ref="virtualScrollerRef"
          class="virtual-scroller"
          :items="messages"
          :min-item-size="200"
          key-field="id"
          :buffer="400"
          @scroll="handleScroll"
        >
          <template #default="{ item: msg, index: msgIndex, active }">
            <DynamicScrollerItem
              :item="msg"
              :active="active"
              :data-index="msgIndex"
              class="virtual-item"
            >
              <div class="message-wrapper">
                <ChatMessage
                  :message="msg"
                  :index="msgIndex"
                  :is-last="msgIndex === messages.length - 1"
                  :is-loading="isLoading"
                  :is-streaming="isStreaming"
                  :is-selected="selectedMessageId && msg.id === selectedMessageId"
                  :show-debug="showDebug"
                  @regenerate="(idx) => emit('regenerate', idx)"
                  @click="(message) => emit('message-click', message)"
                  @delete="(payload) => emit('message-delete', payload)"
                  @continue-research="(taskId) => emit('continue-research', taskId)"
                  @approve="(message) => emit('approve', message)"
                  @reject="(message) => emit('reject', message)"
                />
              </div>
            </DynamicScrollerItem>
          </template>
        </DynamicScroller>

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
              :show-debug="showDebug"
              @regenerate="(idx) => emit('regenerate', idx)"
              @click="(message) => emit('message-click', message)"
              @delete="(payload) => emit('message-delete', payload)"
              @continue-research="(taskId) => emit('continue-research', taskId)"
              @approve="(message) => emit('approve', message)"
              @reject="(message) => emit('reject', message)"
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
          <!-- 首内容到达前（后端尚未产出任何内容）显示"正在准备回复..."，
               收到首个产出（正文/推理/工具调用）后切换为"正在输入..."typing 形态 -->
          <template v-if="hasStreamOutput">
            <div class="typing-dots">
              <span class="typing-dot"></span>
              <span class="typing-dot"></span>
              <span class="typing-dot"></span>
            </div>
            <span class="typing-text">AI正在输入...</span>
          </template>
          <AiLoader v-else type="dots" size="small" text="正在准备回复..." />
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

    <!-- 暂停自动滚底期间的"回到底部"浮标（确有未消费新内容才出现）：点击滚底并恢复跟随 -->
    <Transition name="badge-fade">
      <button
        v-if="showNewContentBadge"
        type="button"
        class="scroll-to-bottom-btn"
        aria-label="回到底部"
        @click="handleScrollToBottomClick"
      >
        <el-icon :size="14"><ArrowDown /></el-icon>
        <span>{{ newContentBadgeText }}</span>
      </button>
    </Transition>
  </div>
</template>

<style scoped>
.chat-main {
  position: relative;
  flex: 1;
  overflow: hidden;
  background-color: var(--background);
}

.messages-container {
  height: 100%;
  overflow-y: auto;
  overscroll-behavior: contain;
  padding: 24px;
  scrollbar-width: thin;
  scrollbar-color: var(--border) transparent;
}

.messages-container::-webkit-scrollbar {
  width: 6px;
}

.messages-container::-webkit-scrollbar-track {
  background: transparent;
}

.messages-container::-webkit-scrollbar-thumb {
  background-color: var(--border);
  border-radius: 3px;
}

.messages-container::-webkit-scrollbar-thumb:hover {
  background-color: var(--muted-foreground);
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--muted-foreground);
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
  background: var(--gradient-primary);
  opacity: 0.1;
  animation: pulse-bg 3s ease-in-out infinite;
}

@keyframes pulse-bg {
  0%, 100% { transform: scale(1); opacity: 0.1; }
  50% { transform: scale(1.12); opacity: 0.05; }
}

.empty-icon {
  position: relative;
  color: var(--sidebar-primary);
}

.empty-title {
  font-size: 24px;
  color: var(--foreground);
  margin-bottom: 8px;
  font-weight: 600;
  letter-spacing: -0.01em;
}

.empty-desc {
  font-size: 14px;
  color: var(--muted-foreground);
  margin-bottom: 0;
  line-height: 1.6;
}

.suggestions-section {
  margin-top: 32px;
  width: 100%;
  max-width: 640px;
}

.suggestions-title {
  font-size: 14px;
  font-weight: 500;
  color: var(--muted-foreground);
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
  border-radius: var(--radius-lg);
  background: var(--card);
  border: 1px solid var(--border);
  cursor: pointer;
  transition: all var(--transition-normal);
  text-align: left;
  font-size: 13px;
  color: var(--foreground);
  line-height: 1.4;
}

.suggestion-card:hover {
  border-color: var(--sidebar-primary);
  background: color-mix(in srgb, var(--sidebar-primary) 6%, transparent);
  transform: translateY(-1px);
  box-shadow: var(--shadow-sm);
}

.suggestion-card:active {
  transform: translateY(0);
}

.suggestion-card.small {
  padding: 8px 12px;
  border-radius: var(--radius);
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

/*
 * DynamicScrollerItem 测量容器（渲染为裸 div，测量取 offsetHeight，不含 margin）。
 * display: flow-root 建立 BFC，阻止 .message-wrapper 的 margin-bottom 折叠穿透，
 * 使消息间距计入测量高度，避免相邻气泡重叠。
 */
.virtual-item {
  display: flow-root;
}

.message-wrapper {
  margin-bottom: 24px;
  will-change: transform;
  padding: 12px 16px;
  border-bottom: 1px solid color-mix(in srgb, var(--border) 50%, transparent);
}

.message-wrapper:last-child {
  border-bottom: none;
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
  background: var(--gradient-primary);
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  flex-shrink: 0;
  margin: 0 12px;
  box-shadow: var(--shadow-sm);
}

.message-content {
  flex: 1;
  min-width: 0;
}

.message-role {
  font-size: 12px;
  color: var(--muted-foreground);
  padding: 0 4px;
  font-weight: 500;
}

.message-text {
  background: var(--card);
  border-radius: var(--radius-lg);
  padding: 14px 18px;
  box-shadow: var(--shadow-sm);
  max-width: 100%;
  word-break: break-word;
  border: 1px solid var(--border);
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
  color: var(--muted-foreground);
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
  background-color: var(--sidebar-primary);
  animation: typing-bounce 1.4s infinite ease-in-out both;
}

.typing-dot:nth-child(1) { animation-delay: -0.32s; }
.typing-dot:nth-child(2) { animation-delay: -0.16s; }

@keyframes typing-bounce {
  0%, 80%, 100% { transform: scale(0); }
  40% { transform: scale(1); }
}

.typing-text {
  color: var(--muted-foreground);
}

/* "回到底部"浮标：绝对定位于消息区底部居中，叠加在滚动内容之上 */
.scroll-to-bottom-btn {
  position: absolute;
  bottom: 20px;
  left: 50%;
  transform: translateX(-50%);
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  border-radius: 999px;
  background: var(--card);
  border: 1px solid var(--border);
  color: var(--foreground);
  font-size: 12px;
  cursor: pointer;
  box-shadow: var(--shadow-md);
  z-index: 10;
  transition: border-color var(--transition-normal), color var(--transition-normal);
}

.scroll-to-bottom-btn:hover {
  border-color: var(--sidebar-primary);
  color: var(--sidebar-primary);
}

.badge-fade-enter-active,
.badge-fade-leave-active {
  transition: opacity var(--transition-normal), transform var(--transition-normal);
}

.badge-fade-enter-from,
.badge-fade-leave-to {
  opacity: 0;
  transform: translateX(-50%) translateY(8px);
}

.dynamic-suggestions {
  max-width: 768px;
  margin: 0 auto;
  padding: 8px 16px 16px;
}

.slide-up-enter-active {
  transition: all var(--transition-normal);
}

.slide-up-enter-from {
  opacity: 0;
  transform: translateY(12px);
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

@media (max-width: 480px) {
  .messages-container {
    padding: 12px 8px;
  }

  .suggestion-card {
    padding: 8px 12px;
    font-size: 13px;
  }

  .message-wrapper {
    padding: 6px 4px;
  }
}
</style>
