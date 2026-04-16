<template>
  <el-button 
    :class="['theme-toggle', className]" 
    :icon="isDark ? Sunny : Moon" 
    :title="isDark ? '切换到浅色模式' : '切换到深色模式'"
    v-bind="$attrs"
    @click="toggleTheme"
  />
</template>

<script setup>
import { computed } from 'vue'
import { Moon, Sunny } from '@element-plus/icons-vue'
import { useThemeStore } from '../stores/theme'

defineProps({
  className: {
    type: String,
    default: ''
  }
})

const themeStore = useThemeStore()
const isDark = computed(() => themeStore.isDark)

const toggleTheme = () => {
  themeStore.toggleTheme()
}
</script>

<style scoped>
.theme-toggle {
  transition: all 0.3s ease;
}
</style>