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
  <el-container v-else class="app-container">
    <AppSidebar v-model:collapse="isCollapse" />
    <el-container class="main-container" :class="{ 'sidebar-collapsed': isCollapse }">
      <div class="header-wrapper" :class="{ 'has-shadow': isScrolled }">
        <AppHeader />
      </div>
      <el-main class="app-main">
        <div class="scroll-container" @scroll="handleScroll">
          <router-view />
        </div>
      </el-main>
    </el-container>
  </el-container>
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
}

.main-container {
  height: 100%;
  overflow: hidden;
  transition: margin-left 0.3s ease;
  display: flex;
  flex-direction: column;
}

.header-wrapper {
  flex-shrink: 0;
  transition: box-shadow 0.2s;
}

.header-wrapper.has-shadow {
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
}

.app-main {
  flex: 1;
  padding: 0;
  overflow: hidden;
  background-color: #fff;
}

.scroll-container {
  height: 100%;
  overflow-y: auto;
}
</style>
