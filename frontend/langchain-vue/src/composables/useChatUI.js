import { ref, computed, watch } from 'vue'
import { useChatStore } from '../stores/chat'
import { useSessionStore } from '../stores/session'
import { ElMessage } from 'element-plus'

export function useChatUI() {
  const chatStore = useChatStore()
  const sessionStore = useSessionStore()

  const showRightPanel = ref(false)
  const showDebug = ref(false)
  const selectedMessage = ref(null)
  const isScrolled = ref(false)

  const messages = computed(() => {
    return sessionStore.getSessionMessages(sessionStore.currentSessionId)
  })

  const hasMessages = computed(() => messages.value && messages.value.length > 0)

  const handleScrollChange = (scrolled) => {
    isScrolled.value = scrolled
  }

  const handleToggleRightPanel = () => {
    showRightPanel.value = !showRightPanel.value
    if (!showRightPanel.value) {
      selectedMessage.value = null
    } else if (!selectedMessage.value) {
      const assistantMessages = messages.value?.filter(m => m.role === 'assistant')
      if (assistantMessages?.length > 0) {
        selectedMessage.value = assistantMessages[assistantMessages.length - 1]
      }
    }
  }

  const handleToggleDebug = () => {
    showDebug.value = !showDebug.value
    if (showDebug.value) {
      ElMessage.success('调试模式已开启 - 消息下方将显示调试信息')
    } else {
      ElMessage.info('调试模式已关闭')
    }
  }

  const handleMessageClick = (message) => {
    if (message.role === 'assistant') {
      selectedMessage.value = message
      if (!showRightPanel.value) {
        showRightPanel.value = true
      }
    }
  }

  const handleRegenerate = async (index) => {
    if (chatStore.isLoading) return
    try {
      await chatStore.regenerateMessage(index)
      ElMessage.success('正在重新生成回复...')
    } catch {
      ElMessage.error('重新生成失败，请稍后重试')
    }
  }

  const handleDelete = async ({ messageId, researchTaskId }) => {
    if (!messageId) return
    const sessionId = sessionStore.currentSessionId
    const msgs = sessionStore.getSessionMessages(sessionId) || []
    const msg = msgs.find(m => m.id === messageId)
    if (!msg) return

    const backendId = msg.backendId
    if (!backendId) {
      ElMessage.warning('消息尚未同步到服务器，请稍后重试')
      return
    }

    try {
      if (msg.role === 'user') {
        await chatStore.deleteMessagePair(sessionId, backendId, messageId)
      } else {
        await chatStore.deleteMessage(backendId, researchTaskId, messageId)
      }
      if (selectedMessage.value?.id === messageId) {
        selectedMessage.value = null
      }
    } catch {
      ElMessage.error('删除失败，请稍后重试')
    }
  }

  watch(
    () => sessionStore.currentSessionId,
    (newSessionId) => {
      if (newSessionId) {
        selectedMessage.value = null
      }
    }
  )

  // 监听新消息，自动选中含丰富内容的助手消息
  watch(() => messages.value?.length, (newLen) => {
    if (newLen > 0) {
      const lastMsg = messages.value[newLen - 1]
      if (lastMsg?.role === 'assistant' && (lastMsg.sources?.length || lastMsg.toolCalls?.length || lastMsg.reasoning)) {
        selectedMessage.value = lastMsg
      }
    }
  })

  return {
    showRightPanel,
    showDebug,
    selectedMessage,
    isScrolled,
    messages,
    hasMessages,
    handleScrollChange,
    handleToggleRightPanel,
    handleToggleDebug,
    handleMessageClick,
    handleRegenerate,
    handleDelete,
  }
}
