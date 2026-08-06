import { onMounted, onUnmounted } from 'vue'

/**
 * 解析聊天输入框的可聚焦元素：
 * - 原生元素（input/textarea）直接返回；
 * - 组件实例（如 ChatInput 未 defineExpose 内部 textarea）回退到 $el 内查找首个可输入元素。
 */
function resolveInputElement(inputRef) {
  const target = inputRef?.value
  if (!target) return null
  if (typeof target.focus === 'function') return target
  const root = target.$el || target
  if (!root?.querySelector) return null
  return root.querySelector('textarea, input') || null
}

export function useChatKeyboard({ onToggleRightPanel, onEscape, inputRef }) {
  const handleKeyDown = (event) => {
    const inputEl = resolveInputElement(inputRef)
    const isInputFocused = !!inputEl && document.activeElement === inputEl

    // Ctrl/Cmd+K：仅当聊天输入框已聚焦时接管（阻止浏览器默认并确保焦点停留），
    // 未聚焦时放行给全局快捷键（useKeyboardShortcuts 打开全局搜索），避免双重触发。
    if ((event.ctrlKey || event.metaKey) && event.key === 'k') {
      if (!isInputFocused) return
      event.preventDefault()
      event.stopImmediatePropagation()
      inputEl.focus()
      return
    }

    // Ctrl/Cmd+B：仅当聊天输入框已聚焦时接管（切换右侧面板），
    // 未聚焦时放行给全局快捷键（切换侧边栏），避免双重触发。
    if ((event.ctrlKey || event.metaKey) && event.key === 'b') {
      if (!isInputFocused) return
      event.preventDefault()
      event.stopImmediatePropagation()
      onToggleRightPanel()
      return
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
