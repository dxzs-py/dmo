import { ref, computed, watch, reactive } from 'vue'
import { useChatStore } from '../stores/chat'
import { useSessionStore } from '../stores/session'
import { useModelStore } from '../stores/model'
import { chatAPI } from '@/api/chat'
import { ElMessage } from 'element-plus'
import { logger } from '../utils/logger'

const MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024

function _toolsStorageKey(sessionId) {
  return `chat_tools_${sessionId || 'default'}`
}

function _loadToolsFromSession(sessionId) {
  try {
    const raw = sessionStorage.getItem(_toolsStorageKey(sessionId))
    if (raw) return JSON.parse(raw)
  } catch {}
  return null
}

function _saveToolsToSession(sessionId, data) {
  try {
    sessionStorage.setItem(_toolsStorageKey(sessionId), JSON.stringify(data))
  } catch {}
}

export function useChatInput() {
  const chatStore = useChatStore()
  const sessionStore = useSessionStore()
  const modelStore = useModelStore()

  const inputMessage = ref('')
  const useWebSearch = ref(false)
  const useDeepThinking = computed({
    get: () => modelStore.thinkingEnabled,
    set: (val) => {
      const paramCfg = modelStore.currentProviderSpecialParams?.thinking
      if (!paramCfg) return
      modelStore.setSpecialParam('thinking', val ? paramCfg.enabledValue : paramCfg.disabledValue)
    },
  })

  const saved = _loadToolsFromSession(sessionStore.currentSessionId)
  const selectedMcpServers = ref(saved?.selectedMcpServers ?? [])
  const selectedTools = ref(saved?.selectedTools ?? [])
  const pendingAttachments = ref([])
  const uploadAbortControllers = ref(new Map())
  const isUploading = ref(false)

  watch([selectedMcpServers, selectedTools], () => {
    _saveToolsToSession(sessionStore.currentSessionId, {
      selectedMcpServers: selectedMcpServers.value,
      selectedTools: selectedTools.value,
    })
  }, { deep: true })

  watch(() => sessionStore.currentSessionId, (newId) => {
    const data = _loadToolsFromSession(newId)
    selectedMcpServers.value = data?.selectedMcpServers ?? []
    selectedTools.value = data?.selectedTools ?? []
  })

  const hasMcpToolSelected = computed(() => selectedMcpServers.value.length > 0)

  const sendMessage = async () => {
    if (pendingAttachments.value.some(att => att.status === 'uploading')) {
      ElMessage.warning('请等待附件上传完成')
      return
    }
    if ((!inputMessage.value.trim() && pendingAttachments.value.length === 0) || chatStore.isLoading) {
      return
    }

    const message = inputMessage.value
    inputMessage.value = ''

    const uploadedAttachmentIds = pendingAttachments.value
      .filter(att => att.status === 'success' && att.id)
      .map(att => att.id)
    const uploadedAttachments = pendingAttachments.value
      .filter(att => att.status === 'success' && att.id)
      .map(att => ({ id: att.id, name: att.name, size: att.size, fileType: att.fileType }))

    pendingAttachments.value = []

    try {
      logger.log('[ChatInput] 发送消息, attachmentIds:', uploadedAttachmentIds)
      await chatStore.sendMessage(message, {
        useTools: true,
        useWebSearch: useWebSearch.value,
        useKnowledgeBase: !!(sessionStore.selectedKnowledgeBase?.id || sessionStore.selectedKnowledgeBases?.length > 0),
        useDeepThinking: useDeepThinking.value,
        useMcp: hasMcpToolSelected.value,
        selectedMcpServers: selectedMcpServers.value.length > 0 ? selectedMcpServers.value : null,
        selectedTools: selectedTools.value.length > 0 ? selectedTools.value : null,
        attachmentIds: uploadedAttachmentIds,
        attachments: uploadedAttachments,
        ...modelStore.getModelConfig(),
      })
    } catch (error) {
      logger.error('发送消息失败:', error)
      ElMessage.error('发送消息失败，请稍后重试')
    }
  }

  const handleSuggestionClick = (suggestion) => {
    if (chatStore.isLoading) return
    inputMessage.value = suggestion
    sendMessage()
  }

  const handleAttach = async (files) => {
    let sessionId = sessionStore.currentSessionId
    if (!sessionId) {
      await sessionStore.createNewSession(chatStore.currentMode)
      sessionId = sessionStore.currentSessionId
    }
    if (!sessionId) return

    const validFiles = []
    for (const file of files) {
      if (file.size > MAX_ATTACHMENT_SIZE) {
        ElMessage.warning(`文件 ${file.name} 超过10MB限制`)
        continue
      }
      validFiles.push(file)
    }

    if (validFiles.length === 0) return

    const uploadTasks = validFiles.map((file) => {
      const tempId = `temp_${Date.now()}_${Math.random().toString(36).slice(2)}`
      const att = reactive({
        tempId,
        id: null,
        name: file.name,
        size: file.size,
        fileType: file.name.split('.').pop().toLowerCase(),
        status: 'uploading',
        progress: 0,
        error: null,
        file,
      })
      pendingAttachments.value.push(att)
      isUploading.value = true

      const controller = new AbortController()
      uploadAbortControllers.value.set(tempId, controller)

      return (async () => {
        try {
          const response = await chatAPI.uploadAttachment(sessionId, file, {
            onUploadProgress: (e) => {
              if (e.total > 0) {
                att.progress = Math.round((e.loaded * 100) / e.total)
              }
            },
            signal: controller.signal,
          })
          Object.assign(att, {
            status: 'success',
            progress: 100,
          })
          const data = response.data?.data
          if (data?.id) {
            att.id = data.id
          }
          delete att.file
        } catch (err) {
          Object.assign(att, {
            status: 'failed',
            error: err.name === 'CanceledError' || err.name === 'AbortError' ? '已取消' : err.message || '上传失败',
          })
        } finally {
          uploadAbortControllers.value.delete(tempId)
          isUploading.value = pendingAttachments.value.some(a => a.status === 'uploading')
        }
      })()
    })

    await Promise.allSettled(uploadTasks)
  }

  const handleRemoveAttachment = async (index) => {
    const att = pendingAttachments.value[index]
    if (!att) return
    if (att.status === 'uploading') return

    if (att.status === 'success' && att.id) {
      try {
        await chatAPI.deleteAttachment(att.id)
      } catch (err) {
        logger.error('删除附件失败:', err)
      }
    }

    pendingAttachments.value.splice(index, 1)
  }

  const cancelUpload = () => {
    for (const [, controller] of uploadAbortControllers.value) {
      controller.abort()
    }
    uploadAbortControllers.value.clear()

    pendingAttachments.value.forEach(att => {
      if (att.status === 'uploading') {
        att.status = 'failed'
        att.error = '已取消'
      }
    })
    isUploading.value = false
  }

  const retryUpload = async (index) => {
    const att = pendingAttachments.value[index]
    if (!att || att.status !== 'failed' || !att.file) return

    const sessionId = sessionStore.currentSessionId
    if (!sessionId) return

    att.status = 'uploading'
    att.progress = 0
    att.error = null

    const controller = new AbortController()
    uploadAbortControllers.value.set(att.tempId, controller)
    isUploading.value = true

    try {
      const response = await chatAPI.uploadAttachment(sessionId, att.file, {
        onUploadProgress: (e) => {
          if (e.total > 0) {
            att.progress = Math.round((e.loaded * 100) / e.total)
          }
        },
        signal: controller.signal,
      })
      att.status = 'success'
      att.progress = 100
      const data = response.data?.data
      if (data?.id) {
        att.id = data.id
      }
      delete att.file
    } catch (err) {
      att.status = 'failed'
      att.error = err.message || '上传失败'
    } finally {
      uploadAbortControllers.value.delete(att.tempId)
      isUploading.value = pendingAttachments.value.some(a => a.status === 'uploading')
    }
  }

  const loadSessionAttachments = async (sessionId) => {
    if (!sessionId) {
      pendingAttachments.value = []
      return
    }
    try {
      const response = await chatAPI.getAttachments(sessionId)
      const data = response.data?.data || []
      pendingAttachments.value = data
        .filter(att => !att.messageId)
        .map(att => ({
          tempId: `loaded_${att.id}`,
          id: att.id,
          name: att.originalName,
          size: att.fileSize,
          fileType: att.fileType,
          status: 'success',
          progress: 100,
          error: null,
        }))
    } catch (err) {
      logger.error('加载附件列表失败:', err)
      pendingAttachments.value = []
    }
  }

  const clearAttachments = () => {
    for (const [, controller] of uploadAbortControllers.value) {
      controller.abort()
    }
    uploadAbortControllers.value.clear()
    pendingAttachments.value = []
    isUploading.value = false
  }

  const handleWebSearchToggle = () => {
    useWebSearch.value = !useWebSearch.value
    ElMessage.success(useWebSearch.value ? '已开启网络搜索' : '已关闭网络搜索')
  }

  return {
    inputMessage,
    useWebSearch,
    useDeepThinking,
    selectedMcpServers,
    selectedTools,
    pendingAttachments,
    hasMcpToolSelected,
    isUploading,
    sendMessage,
    handleSuggestionClick,
    handleAttach,
    handleRemoveAttachment,
    cancelUpload,
    retryUpload,
    handleWebSearchToggle,
    loadSessionAttachments,
    clearAttachments,
  }
}
