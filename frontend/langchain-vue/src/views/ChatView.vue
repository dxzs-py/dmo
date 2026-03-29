<script setup>
import { ref, onMounted, nextTick, watch, computed } from 'vue'
import { useChatStore } from '../stores/chat'
import { useSessionStore } from '../stores/session'
import { User, ChatDotRound, Loading, Refresh } from '@element-plus/icons-vue'
import MarkdownRenderer from '../components/MarkdownRenderer.vue'
import ChainOfThought from '../components/ChainOfThought.vue'
import ToolCallCard from '../components/ToolCallCard.vue'
import ModelSelector from '../components/ModelSelector.vue'
import Sources from '../components/Sources.vue'
import Plan from '../components/Plan.vue'
import { ElMessage } from 'element-plus'

const chatStore = useChatStore()
const sessionStore = useSessionStore()
const inputMessage = ref('')
const messagesContainer = ref(null)
const selectedModel = ref('deepseek-chat')

const messages = computed(() => {
  return sessionStore.getSessionMessages(sessionStore.currentSessionId)
})

const scrollToBottom = () => {
  nextTick(() => {
    if (messagesContainer.value) {
      messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight
    }
  })
}

watch(() => messages.value, () => {
  scrollToBottom()
}, { deep: true })

watch(() => sessionStore.currentSessionId, () => {
  scrollToBottom()
})

const sendMessage = async () => {
  if (!inputMessage.value.trim() || chatStore.isLoading) {
    return
  }
  
  const message = inputMessage.value
  inputMessage.value = ''
  
  try {
    await chatStore.sendMessage(message)
  } catch (error) {
    console.error('发送消息失败:', error)
    ElMessage.error('发送消息失败，请稍后重试')
  }
}

const handleRegenerate = async (index) => {
  if (chatStore.isLoading) {
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

onMounted(() => {
  chatStore.fetchModes()
})
</script>

<template>
  <div class="chat-view">
    <div class="chat-header">
      <div class="header-left">
        <span class="page-title">智能聊天</span>
      </div>
      <div class="header-right">
        <ModelSelector v-model="selectedModel" />
        <el-select v-model="chatStore.currentMode" placeholder="选择模式" style="width: 180px;">
          <el-option
            v-for="(desc, mode) in chatStore.availableModes"
            :key="mode"
            :label="desc"
            :value="mode"
          />
        </el-select>
      </div>
    </div>
    
    <div class="chat-main">
      <div class="messages-container" ref="messagesContainer">
        <div v-if="messages.length === 0" class="empty-state">
          <div class="empty-icon">
            <ChatDotRound :size="64" />
          </div>
          <h3>您今天在想什么？</h3>
          <p>开始一个新的对话，我会尽力帮助您</p>
        </div>
        
        <div v-else class="messages-list">
          <div
            v-for="(msg, index) in messages"
            :key="msg.id || index"
            :class="['message', msg.role]"
          >
            <div class="message-avatar">
              <el-icon v-if="msg.role === 'user'"><User /></el-icon>
              <el-icon v-else><ChatDotRound /></el-icon>
            </div>
            <div class="message-content">
              <div class="message-header">
                <div class="message-role">
                  {{ msg.role === 'user' ? '用户' : 'AI助手' }}
                </div>
                <div v-if="msg.role === 'assistant' && !chatStore.isLoading" class="message-actions">
                  <el-button 
                    type="text" 
                    size="small" 
                    :icon="Refresh"
                    @click="handleRegenerate(index)"
                    title="重新生成"
                  />
                </div>
              </div>
              <div class="message-text">
                <MarkdownRenderer :content="msg.content" />
              </div>
              <Sources
                v-if="msg.sources && msg.sources.length > 0"
                :sources="msg.sources"
                :is-streaming="chatStore.isStreaming && index === messages.length - 1"
              />
              <Plan
                v-if="msg.plan"
                :title="msg.plan.title"
                :description="msg.plan.description"
                :steps="msg.plan.steps"
                :is-streaming="chatStore.isStreaming && index === messages.length - 1"
              />
              <div v-if="msg.chainOfThought" class="message-cot">
                <ChainOfThought :steps="msg.chainOfThought" />
              </div>
              <div v-if="msg.toolCalls && msg.toolCalls.length > 0" class="message-tool-calls">
                <ToolCallCard
                  v-for="(toolCall, idx) in msg.toolCalls"
                  :key="idx"
                  :tool-name="toolCall.name"
                  :input="toolCall.input"
                  :output="toolCall.output"
                  :status="toolCall.status || 'completed'"
                />
              </div>
            </div>
          </div>
          
          <div v-if="chatStore.isLoading && (messages.length === 0 || messages[messages.length - 1].role === 'user')" class="message assistant">
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
    
    <div class="chat-footer">
      <div class="input-area">
        <div class="input-wrapper">
          <el-input
            v-model="inputMessage"
            type="textarea"
            :rows="2"
            :disabled="chatStore.isLoading"
            placeholder="输入您的问题... (Enter 发送，Shift+Enter 换行)"
            @keydown="handleKeyDown"
            resize="none"
          />
        </div>
        <div class="action-buttons">
          <el-button 
            type="primary" 
            @click="sendMessage" 
            :loading="chatStore.isLoading"
            :disabled="!inputMessage.trim()"
          >
            发送
          </el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat-view {
  height: 100%;
  display: flex;
  flex-direction: column;
  background-color: var(--el-bg-color-page);
}

.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: var(--el-bg-color);
  border-bottom: 1px solid var(--el-border-color);
  padding: 12px 24px;
}

.header-left {
  display: flex;
  align-items: center;
}

.page-title {
  font-size: 18px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

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

.chat-footer {
  background: var(--el-bg-color);
  border-top: 1px solid var(--el-border-color);
  padding: 16px 24px;
}

.input-area {
  display: flex;
  gap: 12px;
  align-items: flex-end;
  max-width: 900px;
  margin: 0 auto;
}

.input-wrapper {
  flex: 1;
}

.input-wrapper :deep(.el-textarea__inner) {
  border-radius: 12px;
  padding: 12px 16px;
  font-size: 14px;
  line-height: 1.6;
}

.action-buttons {
  display: flex;
  gap: 8px;
}
</style>
