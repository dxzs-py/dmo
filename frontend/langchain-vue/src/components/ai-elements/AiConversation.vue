<script setup>
import { ref, nextTick, watch } from 'vue'
import { ChatDotRound } from '@element-plus/icons-vue'
import AiMessage from './AiMessage.vue'

const props = defineProps({
  messages: {
    type: Array,
    default: () => []
  },
  isStreaming: {
    type: Boolean,
    default: false
  },
  emptyText: {
    type: String,
    default: '您今天在想什么？'
  }
})

const emit = defineEmits(['message-click', 'scroll-to-bottom'])

const containerRef = ref(null)

const scrollToBottom = () => {
  nextTick(() => {
    if (containerRef.value) {
      containerRef.value.scrollTop = containerRef.value.scrollHeight
    }
  })
}

watch(() => props.messages.length, () => {
  if (props.isStreaming) {
    scrollToBottom()
  }
})

const handleMessageClick = (message) => {
  emit('message-click', message)
}

defineExpose({
  scrollToBottom
})
</script>

<template>
  <div class="ai-conversation" ref="containerRef">
    <div v-if="messages.length === 0" class="ai-conversation__empty">
      <div class="empty-icon">
        <el-icon :size="64"><ChatDotRound /></el-icon>
      </div>
      <h2 class="empty-title">{{ emptyText }}</h2>
    </div>

    <div v-else class="ai-conversation__list">
      <AiMessage
        v-for="(message, index) in messages"
        :key="message.id || index"
        :message="message"
        :is-streaming="isStreaming && index === messages.length - 1"
        @click="handleMessageClick"
      />

      <div v-if="isStreaming" class="ai-conversation__typing">
        <div class="typing-indicator">
          <span class="typing-dot"></span>
          <span class="typing-dot"></span>
          <span class="typing-dot"></span>
        </div>
        <span class="typing-text">AI正在输入...</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ai-conversation {
  height: 100%;
  overflow-y: auto;
  padding: 20px;
}

.ai-conversation__empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  min-height: 300px;
  text-align: center;
}

.empty-icon {
  color: var(--el-text-color-placeholder);
  margin-bottom: 16px;
}

.empty-title {
  font-size: 24px;
  font-weight: 600;
  color: var(--el-text-color-primary);
  margin: 0;
}

.ai-conversation__list {
  max-width: 800px;
  margin: 0 auto;
}

.ai-conversation__typing {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 16px 0;
  color: var(--el-text-color-secondary);
  font-size: 14px;
}

.typing-indicator {
  display: flex;
  gap: 4px;
}

.typing-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background-color: var(--el-color-primary);
  animation: typing-bounce 1.4s infinite ease-in-out both;
}

.typing-dot:nth-child(1) {
  animation-delay: -0.32s;
}

.typing-dot:nth-child(2) {
  animation-delay: -0.16s;
}

@keyframes typing-bounce {
  0%, 80%, 100% {
    transform: scale(0);
  }
  40% {
    transform: scale(1);
  }
}
</style>
