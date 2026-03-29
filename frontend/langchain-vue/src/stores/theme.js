import { defineStore } from 'pinia'
import { ref, watch } from 'vue'

export const useThemeStore = defineStore('theme', () => {
  const theme = ref(localStorage.getItem('theme') || 'light')
  const isDark = ref(theme.value === 'dark')

  const applyTheme = (newTheme) => {
    theme.value = newTheme
    localStorage.setItem('theme', newTheme)
    
    const html = document.documentElement
    if (newTheme === 'dark') {
      html.classList.add('dark')
      isDark.value = true
    } else {
      html.classList.remove('dark')
      isDark.value = false
    }
  }

  const toggleTheme = () => {
    const newTheme = theme.value === 'light' ? 'dark' : 'light'
    applyTheme(newTheme)
  }

  const setTheme = (newTheme) => {
    applyTheme(newTheme)
  }

  watch(theme, (newTheme) => {
    applyTheme(newTheme)
  }, { immediate: true })

  return {
    theme,
    isDark,
    toggleTheme,
    setTheme,
  }
})
