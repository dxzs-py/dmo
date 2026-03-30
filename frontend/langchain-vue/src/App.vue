<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import AppSidebar from './components/AppSidebar.vue'
import AppHeader from './components/AppHeader.vue'
import { useThemeStore } from './stores/theme'

const route = useRoute()
const themeStore = useThemeStore()
const isCollapse = ref(false)
const isScrolled = ref(false)

const isChatUIRoute = computed(() => route.path === '/chat-ui')

const handleScroll = (event) => {
  const scrollTop = event.target.scrollTop
  isScrolled.value = scrollTop > 0
}

onMounted(() => {
  themeStore.setTheme(themeStore.theme)
})
</script>

<template>
  <div v-if="isChatUIRoute" class="chat-ui-wrapper">
    <router-view />
  </div>
  <div v-else class="app-container">
    <AppHeader />
    <div class="content-wrapper">
      <AppSidebar v-model:collapse="isCollapse" />
      <main class="main-content" :class="{ 'sidebar-collapsed': isCollapse }">
        <div class="scroll-container" @scroll="handleScroll">
          <router-view />
        </div>
      </main>
    </div>
  </div>
</template>

<style>
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

html, body, #app {
  height: 100%;
  width: 100%;
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
