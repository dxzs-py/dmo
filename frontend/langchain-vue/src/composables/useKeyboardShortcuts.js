import { onMounted, onUnmounted } from 'vue'

const shortcuts = new Map()
let isInitialized = false

function handleKeydown(e) {
  for (const [name, config] of shortcuts) {
    const { key, ctrl = false, shift = false, alt = false, meta = false, handler, preventDefault = true } = config

    const keyMatch = e.key.toLowerCase() === key.toLowerCase()
    const ctrlMatch = ctrl ? (e.ctrlKey || e.metaKey) : !e.ctrlKey && !e.metaKey
    const shiftMatch = shift ? e.shiftKey : !e.shiftKey
    const altMatch = alt ? e.altKey : !e.altKey

    if (keyMatch && ctrlMatch && shiftMatch && altMatch) {
      if (preventDefault) e.preventDefault()
      handler(e)
      return
    }
  }
}

export function useKeyboardShortcuts(shortcutDefs = {}) {
  onMounted(() => {
    Object.entries(shortcutDefs).forEach(([name, config]) => {
      shortcuts.set(name, config)
    })

    if (!isInitialized) {
      document.addEventListener('keydown', handleKeydown)
      isInitialized = true
    }
  })

  onUnmounted(() => {
    Object.keys(shortcutDefs).forEach(name => {
      shortcuts.delete(name)
    })

    if (shortcuts.size === 0) {
      document.removeEventListener('keydown', handleKeydown)
      isInitialized = false
    }
  })
}

export const COMMON_SHORTCUTS = {
  NEW_CHAT: { key: 'n', ctrl: true, description: '新建对话' },
  SEARCH: { key: 'k', ctrl: true, description: '搜索' },
  SETTINGS: { key: ',', ctrl: true, description: '设置' },
  SEND_MESSAGE: { key: 'Enter', ctrl: true, description: '发送消息' },
  TOGGLE_SIDEBAR: { key: 'b', ctrl: true, description: '切换侧边栏' },
}
