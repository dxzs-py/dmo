import { defineStore } from 'pinia'
import { ref } from 'vue'
import { chatAPI } from '../api/client'
import { useSessionStore } from './session'
import { ElMessage } from 'element-plus'
import { nanoid } from 'nanoid'
import { ChatRequestSchema, validateSchema } from '../lib/validation'

export const useChatStore = defineStore('chat', () => {
  const isLoading = ref(false)
  const isStreaming = ref(false)
  const abortController = ref(null)
  const currentMode = ref('basic-agent')
  const availableModes = ref({
    'basic-agent': '基础代理',
    'rag': 'RAG 检索',
    'workflow': '学习工作流',
    'deep-research': '深度研究',
    'guarded': '安全代理',
  })

  const stopStreaming = () => {
    console.log('stopStreaming 被调用')
    
    if (abortController.value) {
      try {
        abortController.value.abort()
        console.log('abortController 已中止')
      } catch (error) {
        console.error('中止请求时出错:', error)
      }
    }
    
    isStreaming.value = false
    isLoading.value = false
    abortController.value = null
  }

  const sendMessage = async (message, options = {}) => {
    const sessionStore = useSessionStore()
    const sessionId = sessionStore.currentSessionId

    const validation = validateSchema(ChatRequestSchema, {
      message,
      mode: currentMode.value,
      use_tools: options.useTools !== false,
      use_advanced_tools: options.useAdvancedTools || false,
    })

    if (!validation.success) {
      validation.errors.forEach(err => {
        ElMessage.error(err.message)
      })
      return
    }

    if (!sessionId) {
      sessionStore.createNewSession(currentMode.value)
    }

    isLoading.value = true
    isStreaming.value = true
    abortController.value = new AbortController()

    const userMessage = {
      id: nanoid(),
      role: 'user',
      content: message,
      timestamp: new Date().toISOString(),
    }
    sessionStore.addMessageToSession(sessionStore.currentSessionId, userMessage)

    const assistantMessage = {
      id: nanoid(),
      role: 'assistant',
      content: '',
      timestamp: new Date().toISOString(),
      sources: [],
      plan: null,
      chainOfThought: null,
      toolCalls: [],
    }
    sessionStore.addMessageToSession(sessionStore.currentSessionId, assistantMessage, false)

    try {
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
      }, { signal: abortController.value.signal })

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`)
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        if (!isStreaming.value) {
          break
        }
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const dataStr = line.slice(6)
            if (dataStr === '[DONE]') continue

            try {
              const parsed = JSON.parse(dataStr)

              if (parsed.type === 'chunk' && parsed.content) {
                sessionStore.appendToLastMessage(sessionStore.currentSessionId, parsed.content)
              } else if (parsed.content) {
                sessionStore.appendToLastMessage(sessionStore.currentSessionId, parsed.content)
              } else if (parsed.type === 'source' && parsed.data) {
                sessionStore.addSourceToLastMessage(sessionStore.currentSessionId, parsed.data)
              } else if (parsed.type === 'sources' && parsed.data) {
                sessionStore.setSourcesToLastMessage(sessionStore.currentSessionId, parsed.data)
              } else if (parsed.type === 'plan' && parsed.data) {
                sessionStore.setPlanToLastMessage(sessionStore.currentSessionId, parsed.data)
              } else if (parsed.type === 'chainOfThought' && parsed.data) {
                sessionStore.setChainOfThoughtToLastMessage(sessionStore.currentSessionId, parsed.data)
              } else if (parsed.type === 'tool' && parsed.data) {
                sessionStore.addToolCallToLastMessage(sessionStore.currentSessionId, parsed.data)
              }
            } catch (e) {
              console.warn('解析流式数据失败:', e)
            }
          }
        }
      }

      const finalMessages = sessionStore.getSessionMessages(sessionStore.currentSessionId)
      if (finalMessages.length <= 2) {
        const lastMsg = finalMessages[finalMessages.length - 1]
        const title = lastMsg.content.slice(0, 30) + (lastMsg.content.length > 30 ? '...' : '')
        sessionStore.updateSessionTitle(sessionStore.currentSessionId, title)
      }

      sessionStore.syncLastMessageToBackend(sessionStore.currentSessionId).catch(() => {})

    } catch (error) {
      if (error.name === 'AbortError') {
        console.log('请求已取消')
      } else {
        console.error('发送消息失败:', error)

        const messages = sessionStore.getSessionMessages(sessionStore.currentSessionId)

        if (messages.length > 0 && messages[messages.length - 1].role === 'assistant' && messages[messages.length - 1].content === '') {
          const mockResponses = [
            '您好！我是您的智能学习助手。很高兴为您服务！',
            '感谢您的提问！让我为您解答。',
            '这是一个很好的问题！我会尽力为您提供帮助。',
          ]

          const randomResponse = mockResponses[Math.floor(Math.random() * mockResponses.length)]
          let currentContent = ''

          for (let i = 0; i < randomResponse.length; i++) {
            if (!isStreaming.value) {
              break
            }
            await new Promise(resolve => setTimeout(resolve, 30))
            currentContent += randomResponse[i]
            sessionStore.updateLastMessage(sessionStore.currentSessionId, currentContent)
          }
        }

        sessionStore.syncLastMessageToBackend(sessionStore.currentSessionId).catch(() => {})
        ElMessage.error('发送消息失败，请稍后重试')
      }

    } finally {
      isLoading.value = false
      isStreaming.value = false
      abortController.value = null
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

    const assistantMessage = messages[messageIndex]
    if (assistantMessage.role !== 'assistant') {
      return
    }

    const newVersionId = nanoid()
    const newVersion = {
      id: newVersionId,
      content: '',
      sources: [],
      plan: null,
      chainOfThought: null,
      toolCalls: [],
      reasoning: null
    }

    sessionStore.addVersionToMessage(sessionStore.currentSessionId, messageIndex, newVersion)

    isLoading.value = true
    isStreaming.value = true
    abortController.value = new AbortController()

    try {
      const chatHistory = messages.slice(0, messageIndex).map(m => ({
        role: m.role,
        content: m.content,
      }))

      const response = await chatAPI.streamMessage({
        message: userMessage.content,
        chat_history: chatHistory,
        mode: currentMode.value,
        use_tools: true,
        use_advanced_tools: false,
      }, { signal: abortController.value.signal })

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`)
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        if (!isStreaming.value) {
          break
        }
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const dataStr = line.slice(6)
            if (dataStr === '[DONE]') continue

            try {
              const parsed = JSON.parse(dataStr)
              const sessionMessages = sessionStore.getSessionMessages(sessionStore.currentSessionId)
              const msg = sessionMessages[messageIndex]

              if (parsed.type === 'chunk' && parsed.content) {
                if (msg && msg.versions && msg.currentVersion !== undefined) {
                  msg.versions[msg.currentVersion].content += parsed.content
                  msg.content = msg.versions[msg.currentVersion].content
                }
              } else if (parsed.content) {
                if (msg && msg.versions && msg.currentVersion !== undefined) {
                  msg.versions[msg.currentVersion].content += parsed.content
                  msg.content = msg.versions[msg.currentVersion].content
                }
              } else if (parsed.type === 'source' && parsed.data) {
                if (msg && msg.versions && msg.currentVersion !== undefined) {
                  if (!msg.versions[msg.currentVersion].sources) {
                    msg.versions[msg.currentVersion].sources = []
                  }
                  msg.versions[msg.currentVersion].sources.push(parsed.data)
                  msg.sources = msg.versions[msg.currentVersion].sources
                }
              } else if (parsed.type === 'sources' && parsed.data) {
                if (msg && msg.versions && msg.currentVersion !== undefined) {
                  msg.versions[msg.currentVersion].sources = parsed.data
                  msg.sources = msg.versions[msg.currentVersion].sources
                }
              } else if (parsed.type === 'plan' && parsed.data) {
                if (msg && msg.versions && msg.currentVersion !== undefined) {
                  msg.versions[msg.currentVersion].plan = parsed.data
                  msg.plan = msg.versions[msg.currentVersion].plan
                }
              } else if (parsed.type === 'chainOfThought' && parsed.data) {
                if (msg && msg.versions && msg.currentVersion !== undefined) {
                  msg.versions[msg.currentVersion].chainOfThought = parsed.data
                  msg.chainOfThought = msg.versions[msg.currentVersion].chainOfThought
                }
              } else if (parsed.type === 'tool' && parsed.data) {
                if (msg && msg.versions && msg.currentVersion !== undefined) {
                  if (!msg.versions[msg.currentVersion].toolCalls) {
                    msg.versions[msg.currentVersion].toolCalls = []
                  }
                  msg.versions[msg.currentVersion].toolCalls.push(parsed.data)
                  msg.toolCalls = msg.versions[msg.currentVersion].toolCalls
                }
              }
            } catch (e) {
              console.warn('解析流式数据失败:', e)
            }
          }
        }
      }

    } catch (error) {
      if (error.name === 'AbortError') {
        console.log('请求已取消')
      } else {
        console.error('重新生成消息失败:', error)
        ElMessage.error('重新生成失败，请稍后重试')
      }
    } finally {
      isLoading.value = false
      isStreaming.value = false
      abortController.value = null
    }
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
    } catch {
      console.log('使用默认模式列表（后端未连接）')
    }
  }

  const clearCurrentSession = () => {
    const sessionStore = useSessionStore()
    sessionStore.clearCurrentSessionMessages()
  }

  return {
    isLoading,
    isStreaming,
    currentMode,
    availableModes,
    sendMessage,
    regenerateMessage,
    fetchModes,
    clearCurrentSession,
    stopStreaming,
  }
})
