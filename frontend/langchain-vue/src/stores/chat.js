import { defineStore } from 'pinia'
import { ref } from 'vue'
import { chatAPI } from '../api/client'

export const useChatStore = defineStore('chat', () => {
  const messages = ref([])
  const isLoading = ref(false)
  const currentMode = ref('default')
  const availableModes = ref({})

  const addMessage = (role, content) => {
    messages.value.push({
      role,
      content,
      timestamp: new Date().toISOString(),
    })
  }

  const sendMessage = async (message, options = {}) => {
    isLoading.value = true
    
    try {
      addMessage('user', message)
      
      const response = await chatAPI.sendMessage({
        message,
        chat_history: messages.value.slice(0, -1).map(m => ({
          role: m.role,
          content: m.content,
        })),
        mode: currentMode.value,
        use_tools: options.useTools !== false,
        use_advanced_tools: options.useAdvancedTools || false,
        streaming: false,
      })
      
      if (response.data.success) {
        addMessage('assistant', response.data.message)
      } else {
        addMessage('assistant', response.data.message || '抱歉，处理请求时出错')
      }
      
      return response.data
    } catch (error) {
      console.error('发送消息失败:', error)
      addMessage('assistant', '抱歉，网络请求失败，请稍后重试')
      throw error
    } finally {
      isLoading.value = false
    }
  }

  const fetchModes = async () => {
    try {
      const response = await chatAPI.getModes()
      availableModes.value = response.data.modes || {}
      if (response.data.default && !currentMode.value) {
        currentMode.value = response.data.default
      }
    } catch (error) {
      console.error('获取模式列表失败:', error)
    }
  }

  const clearMessages = () => {
    messages.value = []
  }

  return {
    messages,
    isLoading,
    currentMode,
    availableModes,
    addMessage,
    sendMessage,
    fetchModes,
    clearMessages,
  }
})
