import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

export const useThemeStore = defineStore('theme', () => {
  // 主题状态：'light' 或 'dark'
  const currentTheme = ref(localStorage.getItem('theme') || 'light')
  
  // 计算属性：当前主题是否为深色
  const isDark = computed(() => currentTheme.value === 'dark')
  
  // 切换主题
  const toggleTheme = () => {
    currentTheme.value = currentTheme.value === 'light' ? 'dark' : 'light'
    saveThemeToLocalStorage()
    updateDocumentTheme()
  }
  
  // 设置主题
  const setTheme = (theme) => {
    if (['light', 'dark'].includes(theme)) {
      currentTheme.value = theme
      saveThemeToLocalStorage()
      updateDocumentTheme()
    }
  }
  
  // 保存主题到本地存储
  const saveThemeToLocalStorage = () => {
    localStorage.setItem('theme', currentTheme.value)
  }
  
  // 更新文档的主题类
  const updateDocumentTheme = () => {
    if (currentTheme.value === 'dark') {
      document.documentElement.classList.add('dark')
    } else {
      document.documentElement.classList.remove('dark')
    }
  }
  
  // 初始化主题
  const initTheme = () => {
    updateDocumentTheme()
  }
  
  return {
    currentTheme,
    isDark,
    toggleTheme,
    setTheme,
    initTheme
  }
})