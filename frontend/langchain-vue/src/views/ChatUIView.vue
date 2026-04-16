<template>
  <div class="chat-ui-view">
    <aside class="sidebar" :class="{ collapsed: sidebarCollapsed }">
      <div class="sidebar-top">
        <div class="sidebar-header">
          <div v-if="!sidebarCollapsed" class="logo-section">
            <el-icon class="logo-icon"><ChatDotRound /></el-icon>
            <span class="logo-text">ChatUI</span>
          </div>
          <div v-else class="logo-icon-collapsed">
            <el-icon><ChatDotRound /></el-icon>
          </div>
          <el-button 
            class="toggle-btn" 
            link 
            @click="sidebarCollapsed = !sidebarCollapsed"
          >
            <el-icon>
              <ArrowLeft v-if="!sidebarCollapsed" />
              <ArrowRight v-else />
            </el-icon>
          </el-button>
        </div>
        
        <div class="menu-section">
          <el-button class="menu-btn" type="primary" @click="createNewChat">
            <el-icon><Promotion /></el-icon>
            <span v-if="!sidebarCollapsed">新聊天</span>
          </el-button>
        </div>
      </div>
      
      <div v-if="!sidebarCollapsed" class="chat-history">
        <div 
          v-for="chat in chatHistory" 
          :key="chat.id"
          class="chat-item"
          :class="{ active: selectedChat === chat.id }"
          @click="selectChat(chat.id)"
        >
          <el-icon><ChatDotRound /></el-icon>
          <span class="chat-title">{{ chat.title }}</span>
        </div>
      </div>
      
      <div class="sidebar-bottom">
        <div class="user-info">
          <el-avatar :size="32" icon="UserFilled" />
          <div v-if="!sidebarCollapsed" class="user-details">
            <div class="user-name">用户</div>
            <div class="user-status">在线</div>
          </div>
        </div>
      </div>
    </aside>
    
    <main class="main-content">
      <div class="top-bar" :class="{ 'has-shadow': isScrolled }">
        <div class="top-bar-content">
          <span class="page-title">智能助手</span>
          <div class="top-bar-actions">
            <el-dropdown @command="handleTopBarAction">
              <el-button link>
                <el-icon><Tools /></el-icon>
              </el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="clear">
                    <el-icon><Close /></el-icon>
                    清空对话
                  </el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
            <el-button link class="mobile-menu-btn" @click="toggleSidebar">
              <el-icon><Setting /></el-icon>
            </el-button>
          </div>
        </div>
      </div>

      <div ref="messagesContainer" class="chat-area" @scroll="handleScroll">
        <div v-if="messages.length === 0" class="empty-state">
          <div class="empty-icon">
            <el-icon :size="64"><ChatDotRound /></el-icon>
          </div>
          <h2>今天想聊些什么？</h2>
          <p>我是您的智能助手，可以帮您解答问题、编写代码、翻译内容等</p>
        </div>
        
        <div v-else class="messages-list">
          <div
            v-for="(msg, index) in messages"
            :key="msg.id"
            :class="['message', msg.role]"
          >
            <div class="message-avatar">
              <el-icon v-if="msg.role === 'user'"><User /></el-icon>
              <el-icon v-else><ChatDotRound /></el-icon>
            </div>
            <div class="message-content">
              <div class="message-header">
                <span class="message-role">{{ msg.role === 'user' ? '用户' : 'AI助手' }}</span>
                <el-dropdown v-if="msg.role === 'assistant'" trigger="click" @command="(cmd) => handleMessageAction(cmd, index)">
                  <el-button link size="small">
                    <el-icon><More /></el-icon>
                  </el-button>
                  <template #dropdown>
                    <el-dropdown-menu>
                      <el-dropdown-item command="regenerate">
                        <el-icon><Refresh /></el-icon>
                        重新生成
                      </el-dropdown-item>
                    </el-dropdown-menu>
                  </template>
                </el-dropdown>
              </div>
              <div class="message-text">
                <MarkdownRenderer :content="msg.content" />
              </div>
              <div v-if="isStreaming && index === messages.length - 1 && msg.role === 'assistant'" class="streaming-indicator">
                <span class="streaming-dot"></span>
                <span class="streaming-dot"></span>
                <span class="streaming-dot"></span>
              </div>
            </div>
          </div>

          <div v-if="isLoading && (messages.length === 0 || messages[messages.length - 1].role === 'user')" class="message assistant">
            <div class="message-avatar">
              <el-icon><ChatDotRound /></el-icon>
            </div>
            <div class="message-content">
              <div class="message-header">
                <span class="message-role">AI助手</span>
              </div>
              <div class="message-text loading">
                <el-icon class="is-loading"><Loading /></el-icon>
                正在思考<span class="loading-dots">...</span>
              </div>
            </div>
          </div>
        </div>
      </div>
      
      <div class="input-area">
        <div class="input-controls">
          <ModelSelector v-model="selectedModel" @change="handleModelChange" />
        </div>
        <div class="input-wrapper">
          <el-input
            v-model="inputMessage"
            type="textarea"
            :rows="3"
            :disabled="isLoading"
            placeholder="输入您的问题... (Enter 发送，Shift+Enter 换行)"
            resize="none"
            @keydown="handleKeyDown"
          />
        </div>
        <div class="input-actions">
          <el-button
            v-if="isLoading"
            type="warning"
            @click="stopGeneration"
          >
            <el-icon><Loading /></el-icon>
            停止
          </el-button>
          <el-button
            v-else
            type="primary"
            :disabled="!inputMessage.trim()"
            @click="sendMessage"
          >
            发送
          </el-button>
        </div>
      </div>
    </main>
  </div>
</template>

<script setup>
import { ref, computed, nextTick, onMounted, watch } from 'vue'
import { useChatStore } from '../stores/chat'
import { useSessionStore } from '../stores/session'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ChatDotRound,
  Promotion,
  User,
  Loading,
  Setting,
  ArrowLeft,
  ArrowRight,
  Tools,
  Close,
  Refresh,
  More
} from '@element-plus/icons-vue'
import MarkdownRenderer from '../components/MarkdownRenderer.vue'
import ModelSelector from '../components/ModelSelector.vue'

const chatStore = useChatStore()
const sessionStore = useSessionStore()

const sidebarCollapsed = ref(false)
const isScrolled = ref(false)
const inputMessage = ref('')
const selectedChat = ref(null)
const selectedModel = ref('deepseek-chat')
const messagesContainer = ref(null)

const chatHistory = computed(() => sessionStore.sessions)

const messages = computed(() => {
  return sessionStore.getSessionMessages(sessionStore.currentSessionId)
})

const isLoading = computed(() => chatStore.isLoading)
const isStreaming = computed(() => chatStore.isStreaming)

const scrollToBottom = () => {
  nextTick(() => {
    if (messagesContainer.value) {
      messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight
    }
  })
}

watch(() => messages.value.length, () => {
  scrollToBottom()
})

watch(() => sessionStore.currentSessionId, () => {
  scrollToBottom()
})

const toggleSidebar = () => {
  sidebarCollapsed.value = !sidebarCollapsed.value
}

const handleScroll = (event) => {
  isScrolled.value = event.target.scrollTop > 0
}

const createNewChat = () => {
  sessionStore.createNewSession('basic-agent')
  selectedChat.value = sessionStore.currentSessionId
  inputMessage.value = ''
  ElMessage.success('已创建新对话')
}

const selectChat = (chatId) => {
  selectedChat.value = chatId
  sessionStore.switchSession(chatId)
}

const handleModelChange = (model) => {
  selectedModel.value = model
  ElMessage.success(`已切换到 ${model}`)
}

const sendMessage = async () => {
  if (!inputMessage.value.trim() || chatStore.isLoading) {
    return
  }

  const message = inputMessage.value
  inputMessage.value = ''

  try {
    await chatStore.sendMessage(message, {
      useTools: true,
      useAdvancedTools: false
    })
  } catch (error) {
    console.error('发送消息失败:', error)
    ElMessage.error('发送消息失败，请稍后重试')
  }
}

const stopGeneration = () => {
  chatStore.stopStreaming?.()
  ElMessage.info('已停止生成')
}

const regenerateResponse = async (index) => {
  if (chatStore.isLoading) {
    return
  }
  try {
    await chatStore.regenerateMessage(index)
    ElMessage.success('正在重新生成...')
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

const clearChat = () => {
  ElMessageBox.confirm(
    '确定要清空当前对话吗？',
    '提示',
    {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning'
    }
  ).then(() => {
    const session = sessionStore.sessions.find(s => s.id === sessionStore.currentSessionId)
    if (session) {
      session.messages = []
      session.messageCount = 0
    }
    ElMessage.success('对话已清空')
  }).catch(() => {})
}

const handleTopBarAction = (command) => {
  switch (command) {
    case 'clear':
      clearChat()
      break
  }
}

const handleMessageAction = (command, index) => {
  switch (command) {
    case 'regenerate':
      regenerateResponse(index)
      break
  }
}

onMounted(() => {
  if (sessionStore.currentSessionId) {
    selectedChat.value = sessionStore.currentSessionId
  }
  scrollToBottom()
})
</script>

<style scoped>
.chat-ui-view {
  display: flex;
  height: 100vh;
  background-color: #f9f9f9;
}

.sidebar {
  width: 260px;
  background-color: #f9f9f9;
  border-right: 1px solid #e5e7eb;
  display: flex;
  flex-direction: column;
  transition: width 0.3s ease;
  overflow: hidden;
}

.sidebar.collapsed {
  width: 52px;
}

.sidebar-top {
  flex-shrink: 0;
  padding: 12px;
}

.sidebar-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.logo-section {
  display: flex;
  align-items: center;
  gap: 8px;
}

.logo-icon {
  font-size: 24px;
  color: #374151;
}

.logo-icon-collapsed {
  font-size: 24px;
  color: #374151;
  cursor: pointer;
}

.logo-text {
  font-size: 18px;
  font-weight: 600;
  color: #1f2937;
}

.toggle-btn {
  padding: 4px;
}

.menu-section {
  margin-bottom: 16px;
}

.menu-btn {
  width: 100%;
  justify-content: flex-start;
  gap: 8px;
}

.chat-history {
  flex: 1;
  overflow-y: auto;
  padding: 0 8px;
}

.chat-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-radius: 6px;
  cursor: pointer;
  transition: background-color 0.2s;
  margin-bottom: 4px;
}

.chat-item:hover {
  background-color: #f3f4f6;
}

.chat-item.active {
  background-color: #f3f4f6;
}

.chat-title {
  flex: 1;
  font-size: 14px;
  color: #374151;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sidebar-bottom {
  flex-shrink: 0;
  padding: 12px;
  border-top: 1px solid #e5e7eb;
}

.user-info {
  display: flex;
  align-items: center;
  gap: 12px;
}

.user-details {
  flex: 1;
  min-width: 0;
}

.user-name {
  font-size: 14px;
  font-weight: 500;
  color: #1f2937;
}

.user-status {
  font-size: 12px;
  color: #6b7280;
}

.main-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background-color: #ffffff;
}

.top-bar {
  flex-shrink: 0;
  height: 52px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-bottom: 1px solid #e5e7eb;
  transition: box-shadow 0.2s;
}

.top-bar.has-shadow {
  box-shadow: 0 1px 0 rgba(0, 0, 0, 0.05);
}

.top-bar-content {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  max-width: 800px;
  padding: 0 24px;
}

.page-title {
  font-size: 16px;
  font-weight: 500;
  color: #1f2937;
}

.chat-area {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  text-align: center;
}

.empty-icon {
  color: #d1d5db;
  margin-bottom: 24px;
}

.empty-state h2 {
  font-size: 24px;
  font-weight: 600;
  color: #1f2937;
  margin-bottom: 8px;
}

.empty-state p {
  font-size: 14px;
  color: #6b7280;
  max-width: 500px;
}

.messages-list {
  max-width: 800px;
  margin: 0 auto;
}

.message {
  display: flex;
  gap: 16px;
  margin-bottom: 24px;
}

.message.user {
  flex-direction: row-reverse;
}

.message-avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%);
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  flex-shrink: 0;
}

.message.user .message-avatar {
  background: linear-gradient(135deg, #10b981 0%, #059669 100%);
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
  margin-bottom: 4px;
}

.message-role {
  font-size: 12px;
  color: #6b7280;
}

.message-text {
  background: #f9fafb;
  border-radius: 12px;
  padding: 12px 16px;
  max-width: 100%;
  word-break: break-word;
}

.message.user .message-text {
  background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%);
  color: white;
}

.input-area {
  flex-shrink: 0;
  padding: 16px 24px;
  border-top: 1px solid #e5e7eb;
  background-color: #fff;
}

.input-controls {
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  gap: 12px;
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

.input-wrapper :deep(.el-textarea__inner:focus) {
  box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.1);
}

.input-actions {
  display: flex;
  justify-content: flex-end;
}

.streaming-indicator {
  display: flex;
  gap: 4px;
  padding: 8px 0;
}

.streaming-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background-color: #3b82f6;
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

.message-text.loading {
  display: flex;
  align-items: center;
  gap: 8px;
}

.loading-dots {
  animation: dots 1.5s steps(4, end) infinite;
}

@keyframes dots {
  0%, 20% { content: '.'; }
  40% { content: '..'; }
  60%, 100% { content: '...'; }
}

.mobile-menu-btn {
  display: none;
}

@media (max-width: 768px) {
  .mobile-menu-btn {
    display: flex;
  }
}
</style>
