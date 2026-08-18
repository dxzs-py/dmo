<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElLoading } from 'element-plus'
import AppSidebar from './components/layout/AppSidebar.vue'
import AppHeader from './components/layout/AppHeader.vue'
import GlobalSearch from './components/common/GlobalSearch.vue'
import ChatQuickAccess from './components/common/ChatQuickAccess.vue'
import { useThemeStore } from './stores/theme'
import { useUserStore } from './stores/user'
import { useLoadingStore } from './stores/loading'
import { useThrottle } from './composables/useThrottle'

const route = useRoute()
const themeStore = useThemeStore()
const userStore = useUserStore()
const loadingStore = useLoadingStore()
const isCollapse = ref(false)
const isScrolled = ref(false)
const showSearch = ref(false)

let loadingInstance = null

watch(() => loadingStore.isLoading, (newVal) => {
  if (newVal && !loadingInstance) {
    loadingInstance = ElLoading.service({ lock: true, text: '加载中...', background: 'rgba(0, 0, 0, 0.7)' })
  } else if (!newVal && loadingInstance) {
    loadingInstance.close()
    loadingInstance = null
  }
}, { immediate: true })

onUnmounted(() => {
  if (loadingInstance) {
    loadingInstance.close()
    loadingInstance = null
  }
})

const isChatRoute = computed(() => route.path === '/chat')

const cachedViews = ref([])

// 登出时清除 keep-alive 缓存，防止跨用户数据残留
watch(() => userStore.isLoggedIn, (newVal) => {
  if (!newVal) {
    cachedViews.value = []
  }
})

watch(() => route.name, (name) => {
  if (name && route.meta.keepAlive && !cachedViews.value.includes(name)) {
    cachedViews.value.push(name)
  }
}, { immediate: true })

const { throttledFn: handleScrollThrottled } = useThrottle((event) => {
  const scrollTop = event.target.scrollTop
  isScrolled.value = scrollTop > 0
}, 100)

onMounted(async () => {
  try {
    themeStore.setTheme(themeStore.currentTheme)
  } catch (e) {
    console.error('Failed to set theme:', e)
  }
})
</script>

<template>
  <ErrorBoundary>
    <div class="app-container">
      <AppHeader :sidebar-collapsed="isCollapse" @toggle-sidebar="isCollapse = !isCollapse" />
      <div class="content-wrapper">
        <AppSidebar v-model:collapse="isCollapse" />
        <main class="main-content" :class="{ 'sidebar-collapsed': isCollapse, 'chat-main': isChatRoute }">
          <div class="scroll-container" @scroll="handleScrollThrottled">
            <router-view v-slot="{ Component, route: currentRoute }">
              <keep-alive :include="cachedViews" :max="10">
                <component :is="Component" :key="currentRoute.fullPath" />
              </keep-alive>
            </router-view>
          </div>
        </main>
      </div>
    </div>
    <GlobalSearch v-model="showSearch" />
    <ChatQuickAccess />
  </ErrorBoundary>
</template>

<style>
html, body, #app {
  height: 100%;
  width: 100%;
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

.chat-fullscreen-wrapper {
  height: 100%;
  width: 100%;
  overflow: hidden;
}

.app-container {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.content-wrapper {
  flex: 1;
  display: flex;
  overflow: hidden;
}

.main-content {
  flex: 1;
  overflow: hidden;
  background-color: var(--el-bg-color);
  transition: margin-left 0.3s ease;
}

.main-content.sidebar-collapsed {
  margin-left: 0;
}

.scroll-container {
  height: 100%;
  overflow-y: auto;
}

@media (max-width: 1024px) {
  .main-content {
    margin-left: 0;
  }
}

@media (max-width: 768px) {
  .content-wrapper {
    position: relative;
  }
}

@media (max-width: 480px) {
  .scroll-container {
    -webkit-overflow-scrolling: touch;
  }
}
</style>
