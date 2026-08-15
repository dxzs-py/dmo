import { ref, computed, watch } from 'vue'
import { useChatStore } from '../stores/chat'
import { useSessionStore } from '../stores/session'
import { ElMessage, ElMessageBox } from 'element-plus'

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
    // 链式删除进行中（Task 7.4）禁止重新生成（store 层亦有守卫，此处 UI 层提前拦截）
    if (chatStore.isLoading || chatStore.isDeleting) return
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

    // 链式删除：定位该消息所属轮次的 user 消息（assistant 消息向前找最近的 user）
    const msgIdx = msgs.findIndex(m => m.id === messageId)
    let userMsg = msg
    if (msg.role === 'assistant') {
      for (let i = msgIdx; i >= 0; i--) {
        if (msgs[i].role === 'user') { userMsg = msgs[i]; break }
      }
    }
    const userBackendId = userMsg.backendId
    if (!userBackendId) {
      ElMessage.warning('消息尚未同步到服务器，请稍后重试')
      return
    }

    // 是否末轮：该 user 消息之后是否还有对话轮次
    const isLastRound = userMsg === msgs[msgs.length - 1]
      || (msg.role === 'assistant' && msgIdx === msgs.length - 1)

    try {
      if (!isLastRound) {
        await ElMessageBox.confirm(
          '删除该轮会同时删除该轮之后全部对话，确认继续吗？',
          '链式删除确认',
          {
            confirmButtonText: '删除',
            cancelButtonText: '取消',
            type: 'warning',
          }
        )
      }
      await chatStore.deleteMessagePair(sessionId, userBackendId, userMsg.id)
      if (selectedMessage.value?.id === messageId || selectedMessage.value?.id === userMsg.id) {
        selectedMessage.value = null
      }
    } catch (e) {
      if (e === 'cancel' || e === 'close') return
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
