<script setup>
import { useRouter, useRoute } from 'vue-router'
import { useSessionStore } from '../../stores/session'
import { useUserStore } from '../../stores/user'
import { 
  ChatDotRound, 
  Document, 
  Management, 
  Reading, 
  Setting, 
  Plus,
  Close,
  Search,
  Calendar,
  DataAnalysis,
  FolderOpened,
  Files
} from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { confirmDelete } from '../../utils/dialog'
import { computed, ref } from 'vue'

const props = defineProps({
  // 自定义导航菜单项
  menuItems: {
    type: Array,
    default: null
  },
  // 是否显示会话列表
  showSessions: {
    type: Boolean,
    default: true
  },
  // 是否可以折叠
  collapsible: {
    type: Boolean,
    default: true
  },
  // 折叠状态
  collapsed: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['update:collapsed'])

const router = useRouter()
const route = useRoute()
const sessionStore = useSessionStore()
const userStore = useUserStore()

const showSearch = ref(false)
const searchQuery = ref('')

// 默认导航菜单项
const defaultMenuItems = [
  { index: '/chat', label: '智能聊天', icon: ChatDotRound },
  { index: '/rag', label: 'RAG 知识库', icon: Document },
  { index: '/knowledge', label: '知识库管理', icon: FolderOpened },
  { index: '/workflows', label: '学习工作流', icon: Management },
  { index: '/deep-research', label: '深度研究', icon: Reading },
  { index: '/dashboard', label: '数据分析', icon: DataAnalysis },
  { index: '/attachments', label: '附件管理', icon: Files },
  { index: '/settings', label: '设置', icon: Setting },
]

// 使用自定义菜单或默认菜单
const menuItems = computed(() => props.menuItems || defaultMenuItems)

const toggleCollapse = () => {
  if (props.collapsible) {
    emit('update:collapsed', !props.collapsed)
  }
}

const handleMenuSelect = (index) => {
  router.push(index)
}

const handleNewChat = () => {
  sessionStore.createNewSession('basic-agent')
  if (route.path !== '/chat') {
    router.push('/chat')
  }
}

const handleDeleteSession = async (sessionId) => {
  try {
    await confirmDelete('确定要删除这个会话吗？')
    await sessionStore.deleteSession(sessionId)
    ElMessage.success('会话已删除')
  } catch {
    // cancelled
  }
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

const filteredSessions = computed(() => {
  const sessions = sessionStore.sessions
  if (!searchQuery.value.trim()) return sessions
  const q = searchQuery.value.toLowerCase()
  return sessions.filter(s =>
    s.title?.toLowerCase().includes(q)
  )
})

const groupedSessions = computed(() => {
  const groups = {}
  const sessions = filteredSessions.value.slice(0, 30)
  for (const session of sessions) {
    const group = getTimeGroup(session.updatedAt || session.createdAt)
    if (!groups[group]) groups[group] = []
    groups[group].push(session)
  }
  return groups
})

const handleToggleSearch = () => {
  showSearch.value = !showSearch.value
  if (!showSearch.value) searchQuery.value = ''
}
</script>

<template>
  <div class="app-sidebar" :class="{ 'sidebar-collapsed': collapsed }">
    <div class="sidebar-top" v-if="!collapsed">
      <button class="sidebar-new-chat-btn" @click="handleNewChat">
        <el-icon><Plus /></el-icon>
        <span>新建对话</span>
      </button>
      <button v-if="showSessions" class="sidebar-icon-btn" :title="showSearch ? '关闭搜索' : '搜索会话'" @click="handleToggleSearch">
        <el-icon><Search /></el-icon>
      </button>
    </div>
    
    <div v-if="showSearch && !collapsed && showSessions" class="sidebar-search">
      <el-input
        v-model="searchQuery"
        placeholder="搜索会话..."
        clearable
        size="small"
        :prefix-icon="Search"
      />
    </div>
    
    <div class="sidebar-sessions" v-if="!collapsed && showSessions">
      <template v-for="(sessions, group) in groupedSessions" :key="group">
        <div class="session-group">
          <div class="session-group-label">{{ timeGroupLabels[group] }}</div>
          <div
            v-for="session in sessions"
            :key="session.id"
            :class="['session-item', { active: session.id === sessionStore.currentSessionId }]"
            @click="sessionStore.switchSession(session.id)"
          >
            <div class="session-item-content">
              <div class="session-item-title">{{ session.title || '新对话' }}</div>
            </div>
            <button class="session-delete-btn" @click.stop="handleDeleteSession(session.id)">
              <el-icon :size="12"><Close /></el-icon>
            </button>
          </div>
        </div>
      </template>

      <div v-if="filteredSessions.length === 0" class="sidebar-empty">
        <span>{{ searchQuery ? '未找到匹配的会话' : '暂无会话' }}</span>
      </div>
    </div>
    
    <div class="sidebar-bottom">
      <div v-if="!collapsed" class="sidebar-nav-items">
        <button v-for="item in menuItems" :key="item.index" 
                class="sidebar-nav-btn" 
                :class="{ active: route.path === item.index }"
                @click="handleMenuSelect(item.index)">
          <el-icon><component :is="item.icon" /></el-icon>
          <span>{{ item.label }}</span>
        </button>
      </div>
      <div v-else class="sidebar-collapsed-nav">
        <button v-for="item in menuItems" :key="item.index" 
                class="sidebar-nav-btn" 
                :class="{ active: route.path === item.index }"
                v-memo="[route.path === item.index]"
                @click="handleMenuSelect(item.index)">
          <el-icon><component :is="item.icon" /></el-icon>
        </button>
      </div>
      
      <!-- Collapsed mode -->
      <div v-if="collapsed" class="sidebar-collapsed-buttons">
        <button class="sidebar-collapsed-btn" @click="handleNewChat" v-if="showSessions">
          <el-icon><Plus /></el-icon>
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.app-sidebar {
  width: 260px;
  min-width: 260px;
  height: 100%;
  display: flex;
  flex-direction: column;
  background-color: var(--el-bg-color-page);
  border-right: 1px solid var(--el-border-color-lighter);
  overflow: hidden;
  transition: width 0.3s ease;
}

.app-sidebar.sidebar-collapsed {
  width: 64px;
  min-width: 64px;
}

.sidebar-top {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.sidebar-new-chat-btn {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 10px 16px;
  border-radius: 10px;
  border: 1px solid var(--el-border-color);
  background: var(--el-bg-color);
  color: var(--el-text-color-primary);
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s;
}

.sidebar-new-chat-btn:hover {
  background: var(--el-fill-color-light);
  border-color: var(--el-color-primary-light-5);
}

.sidebar-icon-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: 8px;
  border: none;
  background: transparent;
  color: var(--el-text-color-secondary);
  cursor: pointer;
  transition: all 0.2s;
}

.sidebar-icon-btn:hover {
  background: var(--el-fill-color-light);
  color: var(--el-text-color-primary);
}

.sidebar-search {
  padding: 8px 12px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.sidebar-sessions {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
  scrollbar-width: thin;
  scrollbar-color: var(--el-border-color) transparent;
}

.sidebar-sessions::-webkit-scrollbar {
  width: 4px;
}

.sidebar-sessions::-webkit-scrollbar-thumb {
  background-color: var(--el-border-color);
  border-radius: 2px;
}

.session-group {
  margin-bottom: 4px;
}

.session-group-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
  padding: 8px 8px 4px;
  letter-spacing: 0.3px;
}

.session-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 10px;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.15s;
  gap: 8px;
}

.session-item:hover {
  background-color: var(--el-fill-color-light);
}

.session-item.active {
  background-color: var(--el-color-primary-light-9);
}

.session-item-content {
  flex: 1;
  min-width: 0;
}

.session-item-title {
  font-size: 13px;
  color: var(--el-text-color-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  line-height: 1.4;
}

.session-delete-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: var(--el-text-color-placeholder);
  cursor: pointer;
  opacity: 0.4;
  transition: all 0.15s;
  flex-shrink: 0;
}

.session-item:hover .session-delete-btn {
  opacity: 1;
}

.session-delete-btn:hover {
  background: var(--el-color-danger-light-9);
  color: var(--el-color-danger);
}

.sidebar-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 32px 16px;
  font-size: 13px;
  color: var(--el-text-color-placeholder);
}

.sidebar-bottom {
  border-top: 1px solid var(--el-border-color-lighter);
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex-shrink: 0;
}

.sidebar-nav-items {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.sidebar-nav-btn {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-radius: 8px;
  border: none;
  background: transparent;
  color: var(--el-text-color-regular);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s;
  width: 100%;
  text-align: left;
}

.sidebar-nav-btn:hover {
  background: var(--el-fill-color-light);
  color: var(--el-text-color-primary);
}

.sidebar-nav-btn.active {
  background-color: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
}



.sidebar-collapsed-nav {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.sidebar-collapsed-buttons {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  padding-top: 8px;
  border-top: 1px solid var(--el-border-color-lighter);
}

.sidebar-collapsed-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: 10px;
  border: 1px solid var(--el-border-color);
  background: var(--el-bg-color);
  color: var(--el-text-color-primary);
  cursor: pointer;
  transition: all 0.2s;
}

.sidebar-collapsed-btn:hover {
  background: var(--el-fill-color-light);
  border-color: var(--el-color-primary-light-5);
}
</style>
