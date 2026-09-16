import { apiClient } from './axios'

export const modelAPI = {
  getProviders() {
    return apiClient.get('/ai-engine/models/')
  },

  testConnection(providerId, modelName = null) {
    return apiClient.post('/ai-engine/models/test/', {
      providerId: providerId,
      modelName: modelName,
    })
  },

  switchModel(providerId, modelName = null, options = {}) {
    return apiClient.post('/ai-engine/models/switch/', {
      providerId: providerId,
      modelName: modelName,
      temperature: options.temperature,
      maxTokens: options.maxTokens,
      specialParams: options.specialParams || null,
    })
  },

  getHelperModel() {
    return apiClient.get('/ai-engine/helper-model/')
  },

  setHelperModel(providerId = '', modelName = '') {
    return apiClient.put('/ai-engine/helper-model/', {
      providerId: providerId,
      modelName: modelName,
    })
  },

  // AI 设置（统一管理默认模型、辅助模型、Embedding 模型）
  getAISettings() {
    return apiClient.get('/ai-engine/settings/')
  },

  // 沙箱全局可用性（普通用户可读；供"沙箱模式"开关置灰）
  getSandboxAvailability() {
    return apiClient.get('/ai-engine/sandbox-availability/')
  },

  updateAISettings(data) {
    return apiClient.put('/ai-engine/settings/', data)
  },

  rebuildIndexes(providerId, indexNames = []) {
    return apiClient.post('/ai-engine/settings/rebuild-indexes/', {
      providerId: providerId,
      indexNames: indexNames,
    })
  },
}
