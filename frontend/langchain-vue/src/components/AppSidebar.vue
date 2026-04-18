<script setup>
import { useRouter, useRoute } from 'vue-router'
import { useSessionStore } from '../stores/session'
import { 
  ChatDotRound, 
  Document, 
  Management, 
  Reading, 
  Setting, 
  Promotion,
  Close,
  ArrowLeft,
  ArrowRight,
  Clock,
  Sunny,
  Moon,
  Calendar
} from '@element-plus/icons-vue'
import { ElMessageBox, ElMessage } from 'element-plus'
import { computed, ref, onMounted, onUnmounted } from 'vue'

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
const isMobile = ref(false)

const checkMobile = () => {
  isMobile.value = window.innerWidth <= 768
}

onMounted(() => {
  checkMobile()
  window.addEventListener('resize', checkMobile)
})

onUnmounted(() => {
  window.removeEventListener('resize', checkMobile)
})

const toggleCollapse = () => {
  if (isMobile.value) {
    emit('update:collapse', !props.collapse)
  } else {
    emit('update:collapse', !props.collapse)
  }
}

const handleOverlayClick = () => {
  emit('update:collapse', true)
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
      appendToBody: true
    }
  ).then(() => {
    sessionStore.deleteSession(sessionId)
    ElMessage.success('会话已删除')
  }).catch(() => {})
}

function getTimeGroup(timestamp) {
  const now = Date.now()
  const date = new Date(timestamp)
  const today = new Date(now)
  const diffDays = Math.floor((today.setHours(0,0,0,0) - new Date(date).setHours(0,0,0,0)) / (1000 * 60 * 60 * 24))
  
  if (diffDays === 0) return 'today'
  if (diffDays === 1) return 'yesterday'
  if (diffDays < 7) return 'week'
  if (diffDays < 30) return 'month'
  return 'older'
}

const timeGroupLabels = {
  today: '今天',
  yesterday: '昨天',
  week: '近7天',
  month: '近30天',
  older: '更早',
}

const timeGroupIcons = {
  today: Sunny,
  yesterday: Moon,
  week: Clock,
  month: Calendar,
  older: Clock,
}

const groupedSessions = computed(() => {
  const groups = {}
  const sessions = sessionStore.sessions.slice(0, 20)
  for (const session of sessions) {
    const group = getTimeGroup(session.updatedAt || session.createdAt)
    if (!groups[group]) groups[group] = []
    groups[group].push(session)
  }
  return groups
})
</script>

<template>
  <div v-if="isMobile && !collapse" class="sidebar-overlay" @click="handleOverlayClick"></div>
  <div class="app-sidebar" :class="{ 'sidebar-collapsed': collapse, 'sidebar-mobile': isMobile }">
    <div class="sidebar-header">
      <h2 v-if="!collapse">LC-StudyLab</h2>
      <h2 v-else>LC</h2>
      <el-button 
        class="collapse-btn" 
        :icon="collapse ? ArrowRight : ArrowLeft" 
        text
        @click="toggleCollapse"
      />
    </div>
    
    <div class="sidebar-content">
      <el-button 
        v-if="!collapse"
        class="new-chat-btn" 
        type="primary" 
        :icon="Promotion"
        @click="handleNewChat"
      >
        新建对话
      </el-button>
      <el-button 
        v-else
        class="new-chat-btn" 
        type="primary" 
        :icon="Promotion"
        circle
        @click="handleNewChat"
      />
      
      <el-divider />
      
      <div class="nav-menu">
        <el-menu
          :default-active="route.path"
          :ellipsis="false"
          :collapse="collapse"
          @select="handleMenuSelect"
        >
          <el-menu-item v-for="item in menuItems" :key="item.index" :index="item.index">
            <el-icon><component :is="item.icon" /></el-icon>
            <template #title>{{ item.label }}</template>
          </el-menu-item>
        </el-menu>
      </div>
      
      <el-divider v-if="!collapse" />
      
      <div v-if="!collapse" class="session-list">
        <template v-for="(sessions, group) in groupedSessions" :key="group">
          <div class="session-group">
            <div class="session-group-title">
              <el-icon class="group-icon"><component :is="timeGroupIcons[group]" /></el-icon>
              {{ timeGroupLabels[group] }}
            </div>
            <div class="session-items">
              <div
                v-for="session in sessions"
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
                  <Close />
                </el-icon>
              </div>
            </div>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.app-sidebar {
  width: 256px;
  height: 100%;
  background-color: var(--el-bg-color);
  border-right: 1px solid var(--el-border-color-lighter);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  transition: width 0.3s ease;
}

.app-sidebar.sidebar-collapsed {
  width: 64px;
}

.sidebar-header {
  padding: 16px 20px;
  background-color: var(--el-bg-color);
  border-bottom: 1px solid var(--el-border-color-lighter);
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.sidebar-header h2 {
  margin: 0;
  font-size: 18px;
  color: var(--el-text-color-primary);
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.collapse-btn {
  padding: 4px;
  color: var(--el-text-color-secondary);
}

.collapse-btn:hover {
  color: var(--el-color-primary);
}

.sidebar-content {
  flex: 1;
  overflow-y: auto;
  padding: 12px;
  scrollbar-width: thin;
  scrollbar-color: var(--el-border-color) transparent;
}

.sidebar-content::-webkit-scrollbar {
  width: 4px;
}

.sidebar-content::-webkit-scrollbar-track {
  background: transparent;
}

.sidebar-content::-webkit-scrollbar-thumb {
  background-color: var(--el-border-color);
  border-radius: 2px;
}

.new-chat-btn {
  width: 100%;
  justify-content: center;
  border-radius: 12px;
}

.nav-menu {
  margin: 8px 0;
}

.nav-menu .el-menu {
  border-right: none;
  background-color: transparent;
}

.nav-menu :deep(.el-menu-item) {
  border-radius: 8px;
  margin: 2px 0;
  height: 40px;
  line-height: 40px;
}

.session-list {
  margin-top: 4px;
}

.session-group {
  margin-bottom: 8px;
}

.session-group-title {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  font-weight: 600;
  letter-spacing: 0.3px;
  margin-bottom: 6px;
  padding: 0 8px;
  display: flex;
  align-items: center;
  gap: 4px;
}

.group-icon {
  font-size: 14px;
}

.session-items {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.session-item {
  padding: 10px 12px;
  border-radius: 10px;
  cursor: pointer;
  transition: all 0.2s;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.session-item:hover {
  background-color: var(--el-fill-color-light);
}

.session-item.active {
  background-color: var(--el-color-primary-light-9);
}

.session-info {
  flex: 1;
  min-width: 0;
}

.session-title {
  font-size: 13px;
  color: var(--el-text-color-primary);
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.session-meta {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  margin-top: 2px;
}

.delete-icon {
  opacity: 0;
  transition: opacity 0.2s;
  color: var(--el-color-danger);
  font-size: 12px;
  padding: 4px;
}

.session-item:hover .delete-icon {
  opacity: 1;
}

.delete-icon:hover {
  background-color: var(--el-color-danger-light-9);
  border-radius: 4px;
}

.sidebar-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  z-index: 998;
  transition: opacity 0.3s ease;
}

.sidebar-mobile {
  position: fixed;
  left: 0;
  top: 0;
  z-index: 999;
  height: 100vh;
  box-shadow: 4px 0 16px rgba(0, 0, 0, 0.1);
}

.sidebar-mobile.sidebar-collapsed {
  transform: translateX(-100%);
  box-shadow: none;
}

@media (max-width: 768px) {
  .app-sidebar {
    position: fixed;
    left: 0;
    top: 0;
    z-index: 999;
    height: 100vh;
    transform: translateX(0);
    transition: transform 0.3s ease;
  }

  .app-sidebar.sidebar-collapsed {
    transform: translateX(-100%);
    width: 256px;
  }
}
</style>
