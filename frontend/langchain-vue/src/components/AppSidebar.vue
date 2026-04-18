<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import Sidebar from './Sidebar.vue'

const props = defineProps({
  collapse: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['update:collapse'])

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

const handleOverlayClick = () => {
  emit('update:collapse', true)
}
</script>

<template>
  <div v-if="isMobile && !collapse" class="sidebar-overlay" @click="handleOverlayClick"></div>
  <div class="app-sidebar-wrapper" :class="{ 'sidebar-mobile': isMobile, 'sidebar-collapsed': collapse }">
    <Sidebar :collapsed="collapse" :collapsible="false" />
  </div>
</template>

<style scoped>
.sidebar-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  z-index: 998;
  transition: opacity 0.3s ease;
}

.app-sidebar-wrapper {
  height: 100%;
}

.sidebar-mobile {
  position: fixed;
  left: 0;
  top: 0;
  z-index: 999;
  height: 100vh;
  box-shadow: 4px 0 16px rgba(0, 0, 0, 0.1);
  transition: transform 0.3s ease;
}

.sidebar-mobile.sidebar-collapsed {
  transform: translateX(-100%);
  box-shadow: none;
}

@media (max-width: 768px) {
  .app-sidebar-wrapper {
    position: fixed;
    left: 0;
    top: 0;
    z-index: 999;
    height: 100vh;
    transform: translateX(0);
    transition: transform 0.3s ease;
  }

  .app-sidebar-wrapper.sidebar-collapsed {
    transform: translateX(-100%);
  }
}
</style>
