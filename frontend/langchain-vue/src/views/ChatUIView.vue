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
              <DArrowLeft v-if="!sidebarCollapsed" />
              <DArrowRight v-else />
            </el-icon>
          </el-button>
        </div>
        
        <div class="menu-section">
          <el-button class="menu-btn" type="primary" @click="createNewChat">
            <el-icon><Edit /></el-icon>
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
            <el-button link @click="toggleSidebar">
              <el-icon><Menu /></el-icon>
            </el-button>
          </div>
        </div>
      </div>
      
      <div class="chat-area" @scroll="handleScroll">
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
              </div>
              <div class="message-text">
                <MarkdownRenderer :content="msg.content" />
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
              <div class="message-text">
                <el-icon class="is-loading"><Loading /></el-icon> 正在思考...
              </div>
            </div>
          </div>
        </div>
      </div>
      
      <div class="input-area">
        <div class="input-wrapper">
          <el-input
            v-model="inputMessage"
            type="textarea"
            :rows="2"
            :disabled="isLoading"
            placeholder="输入您的问题... (Enter 发送，Shift+Enter 换行)"
            @keydown="handleKeyDown"
            resize="none"
          />
        </div>
        <el-button 
          type="primary" 
          @click="sendMessage" 
          :loading="isLoading"
          :disabled="!inputMessage.trim()"
        >
          发送
        </el-button>
      </div>
    </main>
  </div>
</template>

<script setup>
import { ref, computed, nextTick, onMounted } from 'vue'
import { useChatStore } from '../stores/chat'
import { useSessionStore } from '../stores/session'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { 
  ChatDotRound, 
  Edit, 
  User, 
  UserFilled, 
  Loading, 
  Menu,
  DArrowLeft,
  DArrowRight
} from '@element-plus/icons-vue'
import MarkdownRenderer from '../components/MarkdownRenderer.vue'

const router = useRouter()
const chatStore = useChatStore()
const sessionStore = useSessionStore()

const sidebarCollapsed = ref(false)
const isScrolled = ref(false)
const inputMessage = ref('')
const selectedChat = ref(null)

const chatHistory = ref([
  { id: '1', title: '系统指令解读' },
  { id: '2', title: '学习 Python 基础' },
  { id: '3', title: '代码审查建议' },
  { id: '4', title: 'React Hooks 最佳实践' },
])

const messages = computed(() => {
  return sessionStore.getSessionMessages(sessionStore.currentSessionId)
})

const isLoading = computed(() => chatStore.isLoading)

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
}

const selectChat = (chatId) => {
  selectedChat.value = chatId
  sessionStore.currentSessionId = chatId
}

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

const handleKeyDown = (event) => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    sendMessage()
  }
}

onMounted(() => {
  if (chatHistory.value.length > 0) {
    selectedChat.value = chatHistory.value[0].id
  }
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
  display: flex;
  gap: 12px;
  align-items: flex-end;
}

.input-wrapper {
  flex: 1;
  max-width: 800px;
  margin: 0 auto;
}

.input-wrapper :deep(.el-textarea__inner) {
  border-radius: 12px;
  padding: 12px 16px;
}
</style>
