import { onMounted, onUnmounted } from 'vue'

export function useChatKeyboard({ onToggleRightPanel, onEscape, inputRef }) {
  const handleKeyDown = (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === 'k') {
      event.preventDefault()
      if (inputRef?.value) {
        inputRef.value.focus()
      }
    }
    if ((event.ctrlKey || event.metaKey) && event.key === 'b') {
      event.preventDefault()
      onToggleRightPanel()
    }
    if (event.key === 'Escape' && onEscape) {
      onEscape()
    }
  }

  onMounted(() => {
    document.addEventListener('keydown', handleKeyDown)
  })

  onUnmounted(() => {
    document.removeEventListener('keydown', handleKeyDown)
  })

  return {
    handleKeyDown,
  }
}
