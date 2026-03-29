<script setup>
import { computed } from 'vue'
import { User, ChatDotRound } from '@element-plus/icons-vue'
import MarkdownRenderer from '../MarkdownRenderer.vue'
import ToolCallCard from '../ToolCallCard.vue'
import ChainOfThought from '../ChainOfThought.vue'
import Plan from '../Plan.vue'
import Sources from '../Sources.vue'

const props = defineProps({
  message: {
    type: Object,
    required: true
  },
  isStreaming: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['click'])

const isUser = computed(() => props.message.role === 'user')
const isAssistant = computed(() => props.message.role === 'assistant')

const handleClick = () => {
  emit('click', props.message)
}
</script>

<template>
  <div :class="['ai-message', { user: isUser, assistant: isAssistant }]">
    <div class="ai-message__avatar">
      <el-icon v-if="isUser" :size="24"><User /></el-icon>
      <el-icon v-else :size="24"><ChatDotRound /></el-icon>
    </div>
    <div class="ai-message__content" @click="handleClick">
      <div class="ai-message__header">
        <span class="ai-message__role">
          {{ isUser ? '用户' : 'AI助手' }}
        </span>
      </div>
      <div class="ai-message__body">
        <MarkdownRenderer v-if="message.content" :content="message.content" />

        <div v-if="message.isStreaming" class="ai-message__streaming">
          <span class="streaming-dot"></span>
          <span class="streaming-dot"></span>
          <span class="streaming-dot"></span>
        </div>
      </div>

      <Sources
        v-if="message.sources && message.sources.length > 0"
        :sources="message.sources"
        :is-streaming="isStreaming"
      />

      <Plan
        v-if="message.plan"
        :title="message.plan.title"
        :description="message.plan.description"
        :steps="message.plan.steps"
        :is-streaming="isStreaming"
      />

      <ChainOfThought
        v-if="message.chainOfThought"
        :steps="message.chainOfThought"
      />

      <div v-if="message.toolCalls && message.toolCalls.length > 0" class="ai-message__tools">
        <ToolCallCard
          v-for="(toolCall, index) in message.toolCalls"
          :key="index"
          :tool-name="toolCall.name"
          :input="toolCall.input"
          :output="toolCall.output"
          :status="toolCall.status || 'completed'"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.ai-message {
  display: flex;
  gap: 16px;
  padding: 16px 0;
}

.ai-message.user {
  flex-direction: row-reverse;
}

.ai-message__avatar {
  flex-shrink: 0;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  background-color: var(--el-fill-color-light);
}

.ai-message.assistant .ai-message__avatar {
  background-color: var(--el-color-primary-light-8);
  color: var(--el-color-primary);
}

.ai-message.user .ai-message__avatar {
  background-color: var(--el-color-success-light-8);
  color: var(--el-color-success);
}

.ai-message__content {
  flex: 1;
  max-width: 80%;
  cursor: pointer;
}

.ai-message__header {
  margin-bottom: 8px;
}

.ai-message__role {
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
}

.ai-message__body {
  background-color: var(--el-fill-color-light);
  border-radius: 12px;
  padding: 12px 16px;
  line-height: 1.6;
}

.ai-message.user .ai-message__body {
  background: linear-gradient(135deg, var(--el-color-primary) 0%, var(--el-color-primary-light-3) 100%);
  color: #fff;
}

.ai-message.assistant .ai-message__body {
  background-color: var(--el-fill-color-light);
}

.ai-message__streaming {
  display: inline-flex;
  gap: 4px;
  margin-left: 8px;
}

.streaming-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background-color: var(--el-color-primary);
  animation: bounce 1.4s infinite ease-in-out both;
}

.streaming-dot:nth-child(1) {
  animation-delay: -0.32s;
}

.streaming-dot:nth-child(2) {
  animation-delay: -0.16s;
}

@keyframes bounce {
  0%, 80%, 100% {
    transform: scale(0);
  }
  40% {
    transform: scale(1);
  }
}

.ai-message__tools {
  margin-top: 12px;
}
</style>
