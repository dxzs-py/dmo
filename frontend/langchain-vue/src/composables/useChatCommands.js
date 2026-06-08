import { useChatStore } from '../stores/chat'
import { useSessionStore } from '../stores/session'
import { useModelStore } from '../stores/model'
import { ElMessage } from 'element-plus'

const COMMAND_HANDLERS = {
  help() {
    ElMessage.info('可用命令: /help /status /compact /model /clear /export /version')
  },
  status({ chatStore, sessionStore }) {
    ElMessage.info(`当前模式: ${chatStore.currentMode} | 会话: ${sessionStore.currentSessionId || '无'}`)
  },
  compact() {
    ElMessage.info('会话压缩功能开发中')
  },
  model({ modelStore }) {
    ElMessage.info(`当前模型: ${modelStore.currentModelName || '默认'}`)
  },
  async clear({ sessionStore, clearSelectedMessage }) {
    if (sessionStore.currentSessionId) {
      try {
        const { chatAPI } = await import('../api/chat')
        await chatAPI.clearSessionMessages(sessionStore.currentSessionId)
        sessionStore.clearCurrentSessionMessages()
        clearSelectedMessage()
        ElMessage.success('会话已清除')
      } catch (e) {
        ElMessage.error('清除会话失败: ' + (e.message || '未知错误'))
      }
    } else {
      ElMessage.info('当前无活动会话')
    }
  },
  export() {
    ElMessage.info('导出功能开发中')
  },
  version() {
    ElMessage.info('智能助手 v1.0.0')
  },
}

export function useChatCommands({ clearSelectedMessage } = {}) {
  const chatStore = useChatStore()
  const sessionStore = useSessionStore()
  const modelStore = useModelStore()

  const handleCommandSelect = (cmd) => {
    const handler = COMMAND_HANDLERS[cmd.name]
    if (handler) {
      handler({ chatStore, sessionStore, modelStore, clearSelectedMessage })
    } else {
      ElMessage.info(`命令 /${cmd.name} 已识别`)
    }
  }

  return {
    handleCommandSelect,
  }
}
