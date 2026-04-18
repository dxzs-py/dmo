import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'

export const useThemeStore = defineStore('theme', () => {
  const currentTheme = ref(localStorage.getItem('theme') || 'light')

  const isDark = computed(() => {
    if (currentTheme.value === 'system') {
      return window.matchMedia('(prefers-color-scheme: dark)').matches
    }
    return currentTheme.value === 'dark'
  })

  const effectiveTheme = computed(() => isDark.value ? 'dark' : 'light')

  const toggleTheme = () => {
    const themes = ['light', 'dark', 'system']
    const currentIndex = themes.indexOf(currentTheme.value)
    currentTheme.value = themes[(currentIndex + 1) % themes.length]
    saveThemeToLocalStorage()
    updateDocumentTheme()
  }

  const setTheme = (theme) => {
    if (['light', 'dark', 'system'].includes(theme)) {
      currentTheme.value = theme
      saveThemeToLocalStorage()
      updateDocumentTheme()
    }
  }

  const saveThemeToLocalStorage = () => {
    localStorage.setItem('theme', currentTheme.value)
  }

  const updateDocumentTheme = () => {
    if (isDark.value) {
      document.documentElement.classList.add('dark')
    } else {
      document.documentElement.classList.remove('dark')
    }
    document.documentElement.setAttribute('data-theme', effectiveTheme.value)
  }

  const initTheme = () => {
    updateDocumentTheme()
    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)')
    mediaQuery.addEventListener('change', () => {
      if (currentTheme.value === 'system') {
        updateDocumentTheme()
      }
    })
  }

  return {
    currentTheme,
    isDark,
    effectiveTheme,
    toggleTheme,
    setTheme,
    initTheme
  }
})
