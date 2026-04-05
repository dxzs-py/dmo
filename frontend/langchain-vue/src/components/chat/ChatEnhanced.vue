<script setup>
import { ref, watch, onMounted } from 'vue';
import { useEnhancedChat } from '../../composables/useEnhancedChat';
import EnhancedMessageRenderer from './EnhancedMessageRenderer.vue';
import AiSuggestions from '../ai-elements/AiSuggestions.vue';
import AiSuggestion from '../ai-elements/AiSuggestion.vue';
import ChatHeader from './ChatHeader.vue';
import { ElMessage } from 'element-plus';
import { Close, Search, ChatDotRound } from '@element-plus/icons-vue';

const props = defineProps({
  mode: {
    type: String,
    default: 'default'
  },
  useTools: {
    type: Boolean,
    default: true
  }
});

const emit = defineEmits(['scrollChange', 'regenerate', 'suggestion-click']);

const scrollContainerRef = ref(null);
const isScrolled = ref(false);
const selectedModel = ref('deepseek-chat');
const showDebug = ref(false);
const showRightPanel = ref(true);

const {
  text,
  messages,
  isStreaming,
  error,
  modelSuggestions,
  currentMode,
  availableModes,
  sendMessage,
  stopStreaming,
  clearMessages,
  regenerateMessage,
  loadAvailableModes,
  setMode,
} = useEnhancedChat({
  initialMode: props.mode,
  useTools: props.useTools,
  onError: (err) => {
    ElMessage.error(err.message || '发生错误');
  },
  onStreamStart: () => {
    console.log('Stream started');
  },
  onStreamEnd: () => {
    console.log('Stream ended');
  },
});

const handleSubmit = () => {
  if (!text.value.trim() || isStreaming.value) {
    return;
  }
  sendMessage(text.value);
  text.value = '';
};

const handleSuggestionClick = (suggestion) => {
  sendMessage(suggestion);
  emit('suggestion-click', suggestion);
};

const handleStop = () => {
  stopStreaming();
};

const handleScroll = () => {
  if (scrollContainerRef.value) {
    isScrolled.value = scrollContainerRef.value.scrollTop > 0;
    emit('scrollChange', isScrolled.value);
  }
};

watch(messages, () => {
  if (scrollContainerRef.value) {
    scrollContainerRef.value.scrollTop = scrollContainerRef.value.scrollHeight;
  }
}, { deep: true });

onMounted(() => {
  handleScroll();
  loadAvailableModes();
});

defineExpose({
  regenerateMessage,
  sendMessage,
  clearMessages,
});
</script>

<template>
  <div class="chat-enhanced">
    <ChatHeader
      title="智能聊天"
      :selected-model="selectedModel"
      :current-mode="currentMode"
      :available-modes="availableModes"
      :show-debug="showDebug"
      :show-right-panel="showRightPanel"
      @update:selected-model="selectedModel = $event"
      @update:current-mode="setMode($event)"
      @toggle-debug="showDebug = !showDebug"
      @toggle-right-panel="showRightPanel = !showRightPanel"
    />
    <div
      ref="scrollContainerRef"
      class="messages-container"
      :class="{ 'empty': messages.length === 0 }"
      @scroll="handleScroll"
    >
      <div v-if="messages.length === 0" class="empty-state">
        <div class="empty-icon">
          <el-icon :size="64"><ChatDotRound /></el-icon>
        </div>
        <h2>您今天在想什么？</h2>
      </div>

      <div v-else class="messages-list">
        <div
          v-for="(message, index) in messages"
          :key="message.id"
          class="message-wrapper"
        >
          <EnhancedMessageRenderer
            :message="message"
            :is-streaming="isStreaming && index === messages.length - 1 && message.role === 'assistant'"
          />
        </div>

        <div v-if="!isStreaming && modelSuggestions.length > 0" class="suggestions-section">
          <AiSuggestions>
            <AiSuggestion
              v-for="(suggestion, index) in modelSuggestions"
              :key="index"
              :suggestion="suggestion"
              @click="handleSuggestionClick"
            />
          </AiSuggestions>
        </div>

        <div v-if="error" class="error-section">
          <el-alert
            type="error"
            :title="error.message || '发生错误'"
            :closable="false"
          />
        </div>
      </div>
    </div>

    <div class="input-area">
      <div class="input-container">
        <div class="input-wrapper">
          <el-input
            v-model="text"
            type="textarea"
            :rows="3"
            :disabled="isStreaming"
            :placeholder="isStreaming ? '正在生成回复...' : '输入您的问题...'"
            @keydown="(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                handleSubmit();
              }
            }"
            resize="none"
          />
        </div>

        <div class="input-actions">
          <el-button
            v-if="isStreaming"
            type="warning"
            @click="handleStop"
          >
            <el-icon><Close /></el-icon>
            停止生成
          </el-button>
          <el-button
            v-else
            type="primary"
            @click="handleSubmit"
            :disabled="!text.trim()"
          >
            <el-icon><Search /></el-icon>
            发送
          </el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat-enhanced {
  display: flex;
  flex-direction: column;
  height: 100%;
  background-color: var(--el-bg-color-page);
}

.messages-container {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
}

.messages-container.empty {
  display: flex;
  align-items: center;
  justify-content: center;
}

.empty-state {
  text-align: center;
}

.empty-icon {
  color: #d1d5db;
  margin-bottom: 24px;
}

.empty-state h2 {
  font-size: 24px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.messages-list {
  max-width: 800px;
  margin: 0 auto;
}

.message-wrapper {
  margin-bottom: 24px;
}

.message-wrapper:last-child {
  margin-bottom: 0;
}

.suggestions-section {
  margin-top: 24px;
  padding-top: 16px;
  border-top: 1px solid var(--el-border-color-lighter);
}

.error-section {
  margin-top: 16px;
}

.input-area {
  flex-shrink: 0;
  padding: 16px 24px;
  border-top: 1px solid var(--el-border-color-lighter);
  background-color: var(--el-bg-color);
}

.input-container {
  max-width: 800px;
  margin: 0 auto;
}

.input-wrapper {
  margin-bottom: 12px;
}

.input-wrapper :deep(.el-textarea__inner) {
  border-radius: 12px;
  padding: 12px 16px;
  font-size: 14px;
  line-height: 1.6;
}

.input-actions {
  display: flex;
  justify-content: flex-end;
}
</style>
