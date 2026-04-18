<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import AppSidebar from './components/AppSidebar.vue'
import AppHeader from './components/AppHeader.vue'
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

const isChatRoute = computed(() => route.path === '/chat')

const { throttledFn: handleScrollThrottled } = useThrottle((event) => {
  const scrollTop = event.target.scrollTop
  isScrolled.value = scrollTop > 0
}, 100)

onMounted(async () => {
  themeStore.setTheme(themeStore.currentTheme)
  if (userStore.isLoggedIn) {
    await sessionStore.loadSessionsFromBackend()
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
            <router-view />
          </div>
        </main>
      </div>
    </div>
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
  background-color: #fff;
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
