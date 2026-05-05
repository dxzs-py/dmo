<script setup>
import { ref, computed, watch, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { ElDialog, ElInput, ElScrollbar } from 'element-plus'
import { useKeyboardShortcuts, COMMON_SHORTCUTS } from '../../composables/useKeyboardShortcuts'
import { useSessionStore } from '../../stores/session'
import { useUserStore } from '../../stores/user'

const props = defineProps({
  modelValue: { type: Boolean, default: false }
})

const emit = defineEmits(['update:modelValue'])

const router = useRouter()
const sessionStore = useSessionStore()
const userStore = useUserStore()
const searchQuery = ref('')
const selectedIndex = ref(0)
const inputRef = ref(null)

const pageItems = [
  { label: '智能对话', path: '/chat', icon: '💬', keywords: 'chat 对话 聊天', type: 'page' },
  { label: 'RAG 知识库查询', path: '/rag', icon: '🔍', keywords: 'rag 检索 知识库', type: 'page' },
  { label: '学习工作流', path: '/workflows', icon: '📚', keywords: 'workflow 工作流 学习', type: 'page' },
  { label: '深度研究', path: '/deep-research', icon: '🔬', keywords: 'research 研究 深度', type: 'page' },
  { label: '知识库管理', path: '/knowledge', icon: '📁', keywords: 'knowledge 知识库 管理 文档', type: 'page' },
  { label: '数据分析', path: '/dashboard', icon: '📊', keywords: 'dashboard 数据 分析 统计', type: 'page' },
  { label: '个人资料', path: '/profile', icon: '👤', keywords: 'profile 个人 资料', type: 'page' },
  { label: '设置', path: '/settings', icon: '⚙️', keywords: 'settings 设置 配置', type: 'page' },
]

const sessionItems = computed(() => {
  if (!userStore.isLoggedIn) return []
  return sessionStore.sessions.slice(0, 30).map(s => ({
    label: s.title || '新对话',
    path: `/chat?session=${s.id}`,
    sessionId: s.id,
    icon: '💬',
    keywords: `聊天 会话 chat session ${s.title || ''}`,
    type: 'session',
  }))
})

const allItems = computed(() => {
  return [...pageItems, ...sessionItems.value]
})

const filteredItems = computed(() => {
  if (!searchQuery.value.trim()) return allItems.value
  const q = searchQuery.value.toLowerCase()
  return allItems.value.filter(item =>
    item.label.toLowerCase().includes(q) ||
    item.keywords.toLowerCase().includes(q) ||
    item.path.toLowerCase().includes(q)
  )
})

watch(() => props.modelValue, (val) => {
  if (val) {
    searchQuery.value = ''
    selectedIndex.value = 0
    nextTick(() => inputRef.value?.focus())
  }
})

async function selectItem(item) {
  if (item.type === 'session' && item.sessionId) {
    await sessionStore.switchSession(item.sessionId)
    if (router.currentRoute.value.path !== '/chat') {
      router.push('/chat')
    }
  } else {
    router.push(item.path)
  }
  emit('update:modelValue', false)
}

function handleKeydown(e) {
  if (!props.modelValue) return
  if (e.key === 'ArrowDown') {
    e.preventDefault()
    selectedIndex.value = Math.min(selectedIndex.value + 1, filteredItems.value.length - 1)
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    selectedIndex.value = Math.max(selectedIndex.value - 1, 0)
  } else if (e.key === 'Enter' && filteredItems.value[selectedIndex.value]) {
    e.preventDefault()
    selectItem(filteredItems.value[selectedIndex.value])
  } else if (e.key === 'Escape') {
    emit('update:modelValue', false)
  }
}

useKeyboardShortcuts({
  search: {
    ...COMMON_SHORTCUTS.SEARCH,
    handler: () => emit('update:modelValue', !props.modelValue),
  }
})
</script>

<template>
  <ElDialog
    :model-value="modelValue"
    @update:model-value="emit('update:modelValue', $event)"
    :show-close="false"
    width="520px"
    top="15vh"
    class="search-dialog"
    @keydown="handleKeydown"
  >
    <div class="search-input-wrapper">
      <svg class="search-icon" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2">
        <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
      </svg>
      <ElInput
        ref="inputRef"
        v-model="searchQuery"
        placeholder="搜索页面、功能、聊天记录..."
        size="large"
        :border="false"
        class="search-input"
      />
      <kbd class="search-hint">ESC</kbd>
    </div>
    <ElScrollbar max-height="360px" v-if="filteredItems.length">
      <div class="search-results">
        <template v-for="(item, index) in filteredItems" :key="item.type + '-' + (item.sessionId || item.path)">
          <div
            v-if="index === 0 && item.type === 'session' || (index > 0 && item.type === 'session' && filteredItems[index - 1].type !== 'session')"
            class="search-group-label"
          >聊天记录</div>
          <div
            class="search-item"
            :class="{ selected: index === selectedIndex }"
            v-memo="[item.label, item.path, index === selectedIndex]"
            @click="selectItem(item)"
            @mouseenter="selectedIndex = index"
          >
            <span class="search-item-icon">{{ item.icon }}</span>
            <span class="search-item-label">{{ item.label }}</span>
            <el-tag v-if="item.type === 'session'" size="small" type="info" class="search-item-tag">会话</el-tag>
            <span v-else class="search-item-path">{{ item.path }}</span>
          </div>
        </template>
      </div>
    </ElScrollbar>
    <div v-else class="search-empty">
      未找到匹配结果
    </div>
  </ElDialog>
</template>

<style scoped>
.search-input-wrapper {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0 12px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  margin-bottom: 8px;
}

.search-icon {
  color: var(--el-text-color-secondary);
  flex-shrink: 0;
}

.search-input-wrapper :deep(.el-input__wrapper) {
  box-shadow: none !important;
  background: transparent;
}

.search-hint {
  font-size: 11px;
  padding: 2px 6px;
  border-radius: 4px;
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  color: var(--el-text-color-secondary);
  flex-shrink: 0;
}

.search-results {
  padding: 4px 0;
}

.search-group-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
  padding: 8px 12px 4px;
  letter-spacing: 0.3px;
}

.search-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.15s;
}

.search-item:hover,
.search-item.selected {
  background: var(--el-fill-color-light);
}

.search-item-icon {
  font-size: 18px;
  width: 24px;
  text-align: center;
}

.search-item-label {
  flex: 1;
  font-size: 14px;
  color: var(--el-text-color-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.search-item-tag {
  flex-shrink: 0;
}

.search-item-path {
  font-size: 12px;
  color: var(--el-text-color-placeholder);
}

.search-empty {
  text-align: center;
  padding: 24px;
  color: var(--el-text-color-secondary);
  font-size: 14px;
}
</style>
