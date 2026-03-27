<template>
  <div class="chat-view">
    <el-container>
      <el-header>
        <h2>智能聊天</h2>
        <el-select v-model="chatStore.currentMode" placeholder="选择模式" style="width: 200px; margin-left: 20px;">
          <el-option
            v-for="(desc, mode) in chatStore.availableModes"
            :key="mode"
            :label="desc"
            :value="mode"
          />
        </el-select>
      </el-header>
      
      <el-main>
        <div class="messages-container" ref="messagesContainer">
          <div
            v-for="(msg, index) in chatStore.messages"
            :key="index"
            :class="['message', msg.role]"
          >
            <div class="message-avatar">
              <el-icon v-if="msg.role === 'user'"><User /></el-icon>
              <el-icon v-else><ChatDotRound /></el-icon>
            </div>
            <div class="message-content">
              <div class="message-role">{{ msg.role === 'user' ? '用户' : 'AI助手' }}</div>
              <div class="message-text">{{ msg.content }}</div>
            </div>
          </div>
          
          <div v-if="chatStore.isLoading" class="message assistant">
            <div class="message-avatar">
              <el-icon><ChatDotRound /></el-icon>
            </div>
            <div class="message-content">
              <div class="message-role">AI助手</div>
              <div class="message-text"><el-icon class="is-loading"><Loading /></el-icon> 正在思考...</div>
            </div>
          </div>
        </div>
      </el-main>
      
      <el-footer>
        <div class="input-area">
          <el-input
            v-model="inputMessage"
            type="textarea"
            :rows="2"
            placeholder="输入您的问题..."
            @keydown.enter.ctrl="sendMessage"
            @keydown.enter.meta="sendMessage"
          />
          <el-button type="primary" @click="sendMessage" :loading="chatStore.isLoading">
            发送 (Ctrl+Enter)
          </el-button>
        </div>
      </el-footer>
    </el-container>
  </div>
</template>

<script setup>
import { ref, onMounted, nextTick, watch } from 'vue'
import { useChatStore } from '../stores/chat'
import { User, ChatDotRound, Loading } from '@element-plus/icons-vue'

const chatStore = useChatStore()
const inputMessage = ref('')
const messagesContainer = ref(null)

const scrollToBottom = () => {
  nextTick(() => {
    if (messagesContainer.value) {
      messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight
    }
  })
}

watch(() => chatStore.messages, () => {
  scrollToBottom()
}, { deep: true })

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
  }
}

onMounted(() => {
  chatStore.fetchModes()
})
</script>

<style scoped>
.chat-view {
  height: 100vh;
  display: flex;
  flex-direction: column;
}

.el-container {
  height: 100%;
}

.el-header {
  display: flex;
  align-items: center;
  background: #f5f7fa;
  border-bottom: 1px solid #e4e7ed;
}

.el-header h2 {
  margin: 0;
  font-size: 20px;
  color: #303133;
}

.el-main {
  padding: 0;
  overflow: hidden;
}

.messages-container {
  height: 100%;
  overflow-y: auto;
  padding: 20px;
}

.message {
  display: flex;
  margin-bottom: 20px;
  max-width: 80%;
}

.message.user {
  margin-left: auto;
  flex-direction: row-reverse;
}

.message-avatar {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  background: #409eff;
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  flex-shrink: 0;
  margin: 0 10px;
}

.message.user .message-avatar {
  background: #67c23a;
}

.message-content {
  background: #f5f7fa;
  border-radius: 8px;
  padding: 12px 16px;
}

.message.user .message-content {
  background: #ecf5ff;
}

.message-role {
  font-size: 12px;
  color: #909399;
  margin-bottom: 4px;
}

.message-text {
  font-size: 14px;
  color: #303133;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.el-footer {
  background: #f5f7fa;
  border-top: 1px solid #e4e7ed;
  padding: 15px 20px;
}

.input-area {
  display: flex;
  gap: 10px;
  align-items: flex-end;
}

.input-area .el-textarea {
  flex: 1;
}
</style>
