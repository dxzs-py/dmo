import { defineStore } from 'pinia'
import { ref } from 'vue'
import { chatAPI } from '../api/client'
import { useSessionStore } from './session'

export const useChatStore = defineStore('chat', () => {
  const isLoading = ref(false)
  const isStreaming = ref(false)
  const currentMode = ref('basic-agent')
  const availableModes = ref({
    'basic-agent': '基础代理',
    'rag': 'RAG 检索',
    'workflow': '学习工作流',
    'deep-research': '深度研究',
    'guarded': '安全代理',
  })

  const sendMessage = async (message, options = {}) => {
    const sessionStore = useSessionStore()
    const sessionId = sessionStore.currentSessionId
    
    if (!sessionId) {
      sessionStore.createNewSession(currentMode.value)
    }

    isLoading.value = true
    isStreaming.value = true
    
    try {
      const userMessage = {
        id: Date.now().toString(),
        role: 'user',
        content: message,
        timestamp: new Date().toISOString(),
      }
      sessionStore.addMessageToSession(sessionStore.currentSessionId, userMessage)
      
      const assistantMessage = {
        id: (Date.now() + 1).toString(),
        role: 'assistant',
        content: '',
        timestamp: new Date().toISOString(),
      }
      sessionStore.addMessageToSession(sessionStore.currentSessionId, assistantMessage)
      
      const messages = sessionStore.getSessionMessages(sessionStore.currentSessionId)
      const chatHistory = messages.slice(0, -2).map(m => ({
        role: m.role,
        content: m.content,
      }))
      
      const response = await chatAPI.streamMessage({
        message,
        chat_history: chatHistory,
        mode: currentMode.value,
        use_tools: options.useTools !== false,
        use_advanced_tools: options.useAdvancedTools || false,
      })
      
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`)
      }
      
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let fullContent = ''
      
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        
        const chunk = decoder.decode(value, { stream: true })
        const lines = chunk.split('\n')
        
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const data = line.slice(6)
            if (data === '[DONE]') continue
            
            try {
              const parsed = JSON.parse(data)
              if (parsed.content) {
                fullContent += parsed.content
                sessionStore.updateLastMessage(sessionStore.currentSessionId, fullContent)
              }
              if (parsed.type === 'chunk' && parsed.content) {
                fullContent += parsed.content
                sessionStore.updateLastMessage(sessionStore.currentSessionId, fullContent)
              }
            } catch (e) {
              console.error('解析流式数据失败:', e)
            }
          }
        }
      }
      
      if (fullContent && messages.length <= 2) {
        const title = fullContent.slice(0, 30) + (fullContent.length > 30 ? '...' : '')
        sessionStore.updateSessionTitle(sessionStore.currentSessionId, title)
      }
      
    } catch (error) {
      console.error('发送消息失败:', error)
      
      const sessionStore = useSessionStore()
      const messages = sessionStore.getSessionMessages(sessionStore.currentSessionId)
      
      if (messages.length > 0 && messages[messages.length - 1].role === 'assistant' && messages[messages.length - 1].content === '') {
        const lastMsg = messages[messages.length - 1]
        const mockResponses = [
          '您好！我是您的智能学习助手。很高兴为您服务！由于后端服务暂未启动，这是一条模拟回复。',
          '感谢您的提问！让我为您模拟一个完整的回答。这是在后端服务未启动时的演示功能。',
          '这是一个很好的问题！在实际使用中，我会为您提供详细的解答和相关资料。',
          '我理解您的需求。完整的后端服务将提供强大的AI能力，包括知识检索、工具调用等功能。'
        ]
        
        const randomResponse = mockResponses[Math.floor(Math.random() * mockResponses.length)]
        
        let currentContent = ''
        for (let i = 0; i < randomResponse.length; i++) {
          await new Promise(resolve => setTimeout(resolve, 30))
          currentContent += randomResponse[i]
          sessionStore.updateLastMessage(sessionStore.currentSessionId, currentContent)
        }
        
        if (messages.length <= 2) {
          const title = randomResponse.slice(0, 30) + (randomResponse.length > 30 ? '...' : '')
          sessionStore.updateSessionTitle(sessionStore.currentSessionId, title)
        }
      }
      
    } finally {
      isLoading.value = false
      isStreaming.value = false
    }
  }

  const regenerateMessage = async (messageIndex) => {
    const sessionStore = useSessionStore()
    const messages = sessionStore.getSessionMessages(sessionStore.currentSessionId)
    
    if (messageIndex < 1 || messages.length < 2) {
      return
    }
    
    const userMessage = messages[messageIndex - 1]
    if (userMessage.role !== 'user') {
      return
    }
    
    sessionStore.removeMessagesFromIndex(sessionStore.currentSessionId, messageIndex)
    
    await sendMessage(userMessage.content)
  }

  const fetchModes = async () => {
    try {
      const response = await chatAPI.getModes()
      if (response.data.modes) {
        availableModes.value = response.data.modes
      }
      if (response.data.default && !currentMode.value) {
        currentMode.value = response.data.default
      }
    } catch (error) {
      console.log('使用默认模式列表（后端未连接）')
    }
  }

  return {
    isLoading,
    isStreaming,
    currentMode,
    availableModes,
    sendMessage,
    regenerateMessage,
    fetchModes,
  }
})
