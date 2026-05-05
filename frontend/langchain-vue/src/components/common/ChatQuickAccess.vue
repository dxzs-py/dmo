<script setup>
import { ref, computed, watch, nextTick } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useSessionStore } from '../../stores/session'
import { useUserStore } from '../../stores/user'
import { ChatDotRound, Search, Close, Plus } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'

const router = useRouter()
const route = useRoute()
const sessionStore = useSessionStore()
const userStore = useUserStore()

const isOpen = ref(false)
const searchQuery = ref('')
const searchInputRef = ref(null)

const isChatRoute = computed(() => route.path === '/chat')

const filteredSessions = computed(() => {
  const sessions = sessionStore.sessions
  if (!searchQuery.value.trim()) return sessions.slice(0, 20)
  const q = searchQuery.value.toLowerCase()
  return sessions.filter(s =>
    s.title?.toLowerCase().includes(q)
  ).slice(0, 20)
})

function getTimeGroup(timestamp) {
  const now = Date.now()
  const date = new Date(timestamp)
  const today = new Date(now)
  const diffDays = Math.floor((today.setHours(0, 0, 0, 0) - new Date(date).setHours(0, 0, 0, 0)) / (1000 * 60 * 60 * 24))
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

const groupedSessions = computed(() => {
  const groups = {}
  for (const session of filteredSessions.value) {
    const group = getTimeGroup(session.updatedAt || session.createdAt)
    if (!groups[group]) groups[group] = []
    groups[group].push(session)
  }
  return groups
})

const togglePanel = () => {
  isOpen.value = !isOpen.value
  if (isOpen.value) {
    searchQuery.value = ''
    nextTick(() => searchInputRef.value?.focus())
  }
}

const handleSessionClick = async (sessionId) => {
  await sessionStore.switchSession(sessionId)
  isOpen.value = false
  if (route.path !== '/chat') {
    router.push('/chat')
  }
}

const handleNewChat = async () => {
  await sessionStore.createNewSession('basic-agent')
  isOpen.value = false
  if (route.path !== '/chat') {
    router.push('/chat')
  }
}

const handleDeleteSession = async (sessionId) => {
  try {
    await sessionStore.deleteSession(sessionId)
    ElMessage.success('会话已删除')
  } catch {
    // cancelled
  }
}

watch(isOpen, (val) => {
  if (val && !sessionStore.sessions.length) {
    sessionStore.loadSessionsFromBackend()
  }
})
</script>

<template>
  <div v-if="userStore.isLoggedIn && !isChatRoute" class="chat-quick-access">
    <transition name="quick-panel">
      <div v-if="isOpen" class="quick-access-panel">
        <div class="panel-header">
          <div class="panel-title">
            <el-icon :size="18"><ChatDotRound /></el-icon>
            <span>聊天记录</span>
          </div>
          <button class="panel-close-btn" @click="isOpen = false">
            <el-icon :size="16"><Close /></el-icon>
          </button>
        </div>

        <div class="panel-search">
          <el-input
            ref="searchInputRef"
            v-model="searchQuery"
            placeholder="搜索聊天记录..."
            clearable
            size="small"
            :prefix-icon="Search"
          />
        </div>

        <div class="panel-new-chat">
          <button class="new-chat-btn" @click="handleNewChat">
            <el-icon><Plus /></el-icon>
            <span>新建对话</span>
          </button>
        </div>

        <div class="panel-sessions">
          <template v-for="(sessions, group) in groupedSessions" :key="group">
            <div class="session-group">
              <div class="session-group-label">{{ timeGroupLabels[group] }}</div>
              <div
                v-for="session in sessions"
                :key="session.id"
                class="session-item"
                :class="{ active: session.id === sessionStore.currentSessionId }"
                @click="handleSessionClick(session.id)"
              >
                <div class="session-item-content">
                  <div class="session-item-title">{{ session.title || '新对话' }}</div>
                  <div class="session-item-meta">
                    <span v-if="session.messageCount">{{ session.messageCount }} 条消息</span>
                  </div>
                </div>
                <button class="session-delete-btn" @click.stop="handleDeleteSession(session.id)">
                  <el-icon :size="12"><Close /></el-icon>
                </button>
              </div>
            </div>
          </template>

          <div v-if="filteredSessions.length === 0" class="panel-empty">
            <span>{{ searchQuery ? '未找到匹配的会话' : '暂无聊天记录' }}</span>
          </div>
        </div>
      </div>
    </transition>

    <button class="fab-btn" :class="{ active: isOpen }" @click="togglePanel">
      <el-icon :size="24"><ChatDotRound /></el-icon>
    </button>
  </div>
</template>

<style scoped>
.chat-quick-access {
  position: fixed;
  bottom: 24px;
  right: 24px;
  z-index: 900;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 12px;
}

.fab-btn {
  width: 52px;
  height: 52px;
  border-radius: 50%;
  border: none;
  background: var(--el-color-primary);
  color: #fff;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.15);
  transition: all 0.3s ease;
}

.fab-btn:hover {
  transform: scale(1.08);
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.2);
}

.fab-btn.active {
  background: var(--el-color-danger);
  transform: rotate(0deg);
}

.quick-access-panel {
  width: 340px;
  max-height: 480px;
  background: var(--el-bg-color);
  border-radius: 16px;
  border: 1px solid var(--el-border-color-lighter);
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.12);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 16px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.panel-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.panel-close-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--el-text-color-secondary);
  cursor: pointer;
  transition: all 0.15s;
}

.panel-close-btn:hover {
  background: var(--el-fill-color-light);
  color: var(--el-text-color-primary);
}

.panel-search {
  padding: 8px 12px;
}

.panel-new-chat {
  padding: 0 12px 8px;
}

.new-chat-btn {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 8px 12px;
  border-radius: 8px;
  border: 1px dashed var(--el-border-color);
  background: transparent;
  color: var(--el-text-color-regular);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s;
}

.new-chat-btn:hover {
  border-color: var(--el-color-primary-light-5);
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}

.panel-sessions {
  flex: 1;
  overflow-y: auto;
  padding: 4px 8px 8px;
  scrollbar-width: thin;
  scrollbar-color: var(--el-border-color) transparent;
}

.panel-sessions::-webkit-scrollbar {
  width: 4px;
}

.panel-sessions::-webkit-scrollbar-thumb {
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
  padding: 6px 8px 4px;
  letter-spacing: 0.3px;
}

.session-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 10px;
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

.session-item-meta {
  font-size: 11px;
  color: var(--el-text-color-placeholder);
  margin-top: 2px;
}

.session-delete-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: var(--el-text-color-placeholder);
  cursor: pointer;
  opacity: 0;
  transition: all 0.15s;
  flex-shrink: 0;
}

.session-item:hover .session-delete-btn {
  opacity: 0.6;
}

.session-delete-btn:hover {
  opacity: 1 !important;
  background: var(--el-color-danger-light-9);
  color: var(--el-color-danger);
}

.panel-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 32px 16px;
  font-size: 13px;
  color: var(--el-text-color-placeholder);
}

.quick-panel-enter-active {
  transition: all 0.25s ease;
}

.quick-panel-leave-active {
  transition: all 0.2s ease;
}

.quick-panel-enter-from {
  opacity: 0;
  transform: translateY(12px) scale(0.95);
}

.quick-panel-leave-to {
  opacity: 0;
  transform: translateY(8px) scale(0.97);
}

@media (max-width: 768px) {
  .chat-quick-access {
    bottom: 16px;
    right: 16px;
  }

  .quick-access-panel {
    width: calc(100vw - 32px);
    max-height: 60vh;
  }

  .fab-btn {
    width: 48px;
    height: 48px;
  }
}
</style>
