<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppSidebar from './components/layout/AppSidebar.vue'
import AppHeader from './components/layout/AppHeader.vue'
import GlobalSearch from './components/common/GlobalSearch.vue'
import { useThemeStore } from './stores/theme'
import { useSessionStore } from './stores/session'
import { useUserStore } from './stores/user'
import { useThrottle } from './composables/useDebounce'

const route = useRoute()
const themeStore = useThemeStore()
const sessionStore = useSessionStore()
const userStore = useUserStore()
const isCollapse = ref(false)
const isScrolled = ref(false)
const showSearch = ref(false)

const isChatRoute = computed(() => route.path === '/chat')

const cachedViews = ref([])

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
  themeStore.setTheme(themeStore.currentTheme)
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
</style>
