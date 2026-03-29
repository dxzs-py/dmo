import { generateId, createUserMessage, createAssistantMessage } from './types.js'

class MessageManager {
  constructor() {
    this.messages = []
    this.listeners = []
  }

  addMessage(message) {
    const msg = {
      ...message,
      id: message.id || generateId(),
      timestamp: message.timestamp || new Date().toISOString(),
    }
    this.messages.push(msg)
    this.notifyListeners()
    return msg
  }

  addUserMessage(content) {
    return this.addMessage(createUserMessage(content))
  }

  addAssistantMessage(content = '', extra = {}) {
    return this.addMessage(createAssistantMessage(content, extra))
  }

  updateMessage(messageId, updates) {
    const index = this.messages.findIndex(m => m.id === messageId)
    if (index !== -1) {
      this.messages[index] = { ...this.messages[index], ...updates }
      this.notifyListeners()
      return this.messages[index]
    }
    return null
  }

  appendContent(messageId, content) {
    const message = this.messages.find(m => m.id === messageId)
    if (message) {
      message.content = (message.content || '') + content
      this.notifyListeners()
      return message
    }
    return null
  }

  getMessage(messageId) {
    return this.messages.find(m => m.id === messageId)
  }

  getAllMessages() {
    return [...this.messages]
  }

  removeMessage(messageId) {
    const index = this.messages.findIndex(m => m.id === messageId)
    if (index !== -1) {
      this.messages.splice(index, 1)
      this.notifyListeners()
      return true
    }
    return false
  }

  clearMessages() {
    this.messages = []
    this.notifyListeners()
  }

  getLastMessage() {
    return this.messages.length > 0 ? this.messages[this.messages.length - 1] : null
  }

  getMessagesByRole(role) {
    return this.messages.filter(m => m.role === role)
  }

  setMessages(messages) {
    this.messages = messages.map(m => ({
      ...m,
      id: m.id || generateId(),
      timestamp: m.timestamp || new Date().toISOString(),
    }))
    this.notifyListeners()
  }

  subscribe(callback) {
    this.listeners.push(callback)
    return () => {
      const index = this.listeners.indexOf(callback)
      if (index !== -1) {
        this.listeners.splice(index, 1)
      }
    }
  }

  notifyListeners() {
    this.listeners.forEach(callback => {
      try {
        callback(this.getAllMessages())
      } catch (e) {
        console.error('Error in message listener:', e)
      }
    })
  }
}

let managerInstance = null

export function createMessageManager() {
  if (!managerInstance) {
    managerInstance = new MessageManager()
  }
  return managerInstance
}

export function getMessageManager() {
  if (!managerInstance) {
    managerInstance = new MessageManager()
  }
  return managerInstance
}

export { MessageManager }
