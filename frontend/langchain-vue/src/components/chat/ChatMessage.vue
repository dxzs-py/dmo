<script setup>
import { User, ChatDotRound, Refresh } from '@element-plus/icons-vue'
import MarkdownRenderer from '../MarkdownRenderer.vue'
import ChainOfThought from '../ChainOfThought.vue'
import ToolCallCard from '../ToolCallCard.vue'
import Sources from '../Sources.vue'
import Plan from '../Plan.vue'
import { AiReasoning } from '../ai-elements'

const props = defineProps({
  message: {
    type: Object,
    required: true,
    validator: (value) => {
      if (!value || typeof value !== 'object') return false
      const validRoles = ['user', 'assistant', 'system']
      if (!validRoles.includes(value.role)) {
        console.warn(`ChatMessage: invalid role "${value.role}"`)
        return false
      }
      return true
    }
  },
  index: {
    type: Number,
    required: true,
    validator: (value) => value >= 0
  },
  isLast: {
    type: Boolean,
    default: false
  },
  isLoading: {
    type: Boolean,
    default: false
  },
  isStreaming: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits({
  regenerate: (index) => typeof index === 'number' && index >= 0
})

function handleRegenerate() {
  emit('regenerate', props.index)
}
</script>

<template>
  <div :class="['message', message.role]">
    <div class="message-avatar">
      <el-icon v-if="message.role === 'user'"><User /></el-icon>
      <el-icon v-else><ChatDotRound /></el-icon>
    </div>
    <div class="message-content">
      <div class="message-header">
        <div class="message-role">
          {{ message.role === 'user' ? '用户' : 'AI助手' }}
        </div>
        <div v-if="message.role === 'assistant' && !isLoading" class="message-actions">
          <el-button
            link
            size="small"
            :icon="Refresh"
            title="重新生成"
            @click="handleRegenerate"
          />
        </div>
      </div>
      <div class="message-text">
        <MarkdownRenderer :content="message.content" />
      </div>
      <AiReasoning
        v-if="message.reasoning && message.reasoning.content"
        :content="message.reasoning.content"
        :duration="message.reasoning.duration"
        :is-streaming="isStreaming && isLast"
      />
      <Sources
        v-if="message.sources && message.sources.length > 0"
        :sources="message.sources"
        :is-streaming="isStreaming && isLast"
      />
      <Plan
        v-if="message.plan"
        :title="message.plan.title"
        :description="message.plan.description"
        :steps="message.plan.steps"
        :is-streaming="isStreaming && isLast"
      />
      <div v-if="message.chainOfThought" class="message-cot">
        <ChainOfThought :steps="message.chainOfThought" />
      </div>
      <div v-if="message.toolCalls && message.toolCalls.length > 0" class="message-tool-calls">
        <ToolCallCard
          v-for="(toolCall, idx) in message.toolCalls"
          :key="idx"
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
.message {
  display: flex;
  margin-bottom: 24px;
  max-width: 100%;
}

.message.user {
  flex-direction: row-reverse;
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

.message.user .message-avatar {
  background: linear-gradient(135deg, var(--el-color-success) 0%, var(--el-color-success-light-3) 100%);
}

.message-content {
  flex: 1;
  min-width: 0;
}

.message.user .message-content {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
}

.message-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}

.message-actions {
  opacity: 0;
  transition: opacity 0.2s;
}

.message:hover .message-actions {
  opacity: 1;
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

.message.user .message-text {
  background: linear-gradient(135deg, var(--el-color-primary) 0%, var(--el-color-primary-light-3) 100%);
  color: white;
}

.message.user .message-text :deep(code) {
  background-color: rgba(255, 255, 255, 0.2);
  color: white;
}

.message.user .message-text :deep(pre) {
  background-color: rgba(0, 0, 0, 0.1);
}

.message.user .message-text :deep(pre code) {
  background-color: transparent;
}

.message-cot,
.message-tool-calls {
  margin-top: 12px;
}
</style>
