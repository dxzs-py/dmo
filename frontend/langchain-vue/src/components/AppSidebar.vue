<script setup>
import { useRouter, useRoute } from 'vue-router'
import { useSessionStore } from '../stores/session'
import { 
  ChatDotRound, 
  Document, 
  Management, 
  Reading, 
  Setting, 
  Plus,
  Delete,
  Fold,
  Expand
} from '@element-plus/icons-vue'
import { ElMessageBox, ElMessage } from 'element-plus'

const props = defineProps({
  collapse: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['update:collapse'])

const router = useRouter()
const route = useRoute()
const sessionStore = useSessionStore()

const toggleCollapse = () => {
  emit('update:collapse', !props.collapse)
}

const menuItems = [
  { index: '/chat', label: '智能聊天', icon: ChatDotRound },
  { index: '/rag', label: 'RAG 知识库', icon: Document },
  { index: '/workflows', label: '学习工作流', icon: Management },
  { index: '/deep-research', label: '深度研究', icon: Reading },
  { index: '/settings', label: '设置', icon: Setting },
]

const handleMenuSelect = (index) => {
  router.push(index)
}

const handleNewChat = () => {
  sessionStore.createNewSession('basic-agent')
  if (route.path !== '/chat') {
    router.push('/chat')
  }
}

const handleDeleteSession = (sessionId, event) => {
  event.stopPropagation()
  ElMessageBox.confirm(
    '确定要删除这个会话吗？',
    '提示',
    {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning',
    }
  ).then(() => {
    sessionStore.deleteSession(sessionId)
    ElMessage.success('会话已删除')
  }).catch(() => {})
}
</script>

<template>
  <div class="app-sidebar" :class="{ 'sidebar-collapsed': collapse }">
    <div class="sidebar-header">
      <h2 v-if="!collapse">LC-StudyLab</h2>
      <h2 v-else>LC</h2>
      <el-button 
        class="collapse-btn" 
        :icon="collapse ? Expand : Fold" 
        @click="toggleCollapse"
        text
      />
    </div>
    
    <div class="sidebar-content">
      <el-button 
        v-if="!collapse"
        class="new-chat-btn" 
        type="primary" 
        @click="handleNewChat"
        :icon="Plus"
      >
        新建对话
      </el-button>
      <el-button 
        v-else
        class="new-chat-btn" 
        type="primary" 
        @click="handleNewChat"
        :icon="Plus"
        circle
      />
      
      <el-divider />
      
      <div class="nav-menu">
        <el-menu
          :default-active="route.path"
          @select="handleMenuSelect"
          :ellipsis="false"
          :collapse="collapse"
        >
          <el-menu-item v-for="item in menuItems" :key="item.index" :index="item.index">
            <el-icon><component :is="item.icon" /></el-icon>
            <template #title>{{ item.label }}</template>
          </el-menu-item>
        </el-menu>
      </div>
      
      <el-divider v-if="!collapse" />
      
      <div v-if="!collapse" class="session-list">
        <div class="session-list-title">最近会话</div>
        <div class="session-items">
          <div
            v-for="session in sessionStore.sessions.slice(0, 10)"
            :key="session.id"
            :class="['session-item', { active: session.id === sessionStore.currentSessionId }]"
            @click="sessionStore.switchSession(session.id)"
          >
            <div class="session-info">
              <div class="session-title">{{ session.title }}</div>
              <div class="session-meta">
                {{ sessionStore.getModeLabel(session.mode) }} · {{ session.messageCount }} 条
              </div>
            </div>
            <el-icon 
              class="delete-icon" 
              @click="handleDeleteSession(session.id, $event)"
            >
              <Delete />
            </el-icon>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.app-sidebar {
  width: 256px;
  height: 100%;
  background-color: #f5f7fa;
  border-right: 1px solid #e4e7ed;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  transition: width 0.3s ease;
}

.app-sidebar.sidebar-collapsed {
  width: 64px;
}

.sidebar-header {
  padding: 20px;
  background-color: #fff;
  border-bottom: 1px solid #e4e7ed;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.sidebar-header h2 {
  margin: 0;
  font-size: 20px;
  color: #303133;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.collapse-btn {
  padding: 4px;
  color: #909399;
}

.collapse-btn:hover {
  color: #409eff;
}

.sidebar-content {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
}

.new-chat-btn {
  width: 100%;
  justify-content: center;
}

.nav-menu {
  margin: 16px 0;
}

.nav-menu .el-menu {
  border-right: none;
  background-color: transparent;
}

.session-list {
  margin-top: 16px;
}

.session-list-title {
  font-size: 12px;
  color: #909399;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 8px;
  padding: 0 8px;
}

.session-items {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.session-item {
  padding: 12px;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.session-item:hover {
  background-color: #e4e7ed;
}

.session-item.active {
  background-color: #ecf5ff;
}

.session-info {
  flex: 1;
  min-width: 0;
}

.session-title {
  font-size: 14px;
  color: #303133;
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.session-meta {
  font-size: 12px;
  color: #909399;
  margin-top: 4px;
}

.delete-icon {
  opacity: 0;
  transition: opacity 0.2s;
  color: #f56c6c;
  font-size: 14px;
  padding: 4px;
}

.session-item:hover .delete-icon {
  opacity: 1;
}

.delete-icon:hover {
  background-color: rgba(245, 108, 108, 0.1);
  border-radius: 4px;
}
</style>
