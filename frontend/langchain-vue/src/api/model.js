import { apiClient } from './axios'

export const modelAPI = {
  getProviders() {
    return apiClient.get('/ai-engine/models/')
  },

  testConnection(providerId, modelName = null) {
    return apiClient.post('/ai-engine/models/test/', {
      provider_id: providerId,
      model_name: modelName,
    })
  },

  switchModel(providerId, modelName = null, options = {}) {
    return apiClient.post('/ai-engine/models/switch/', {
      provider_id: providerId,
      model_name: modelName,
      temperature: options.temperature,
      max_tokens: options.max_tokens,
      special_params: options.special_params || null,
    })
  },

  getHelperModel() {
    return apiClient.get('/ai-engine/helper-model/')
  },

  setHelperModel(providerId = '', modelName = '') {
    return apiClient.put('/ai-engine/helper-model/', {
      provider_id: providerId,
      model_name: modelName,
    })
  },

  // AI 设置（统一管理默认模型、辅助模型、Embedding 模型）
  getAISettings() {
    return apiClient.get('/ai-engine/settings/')
  },

  updateAISettings(data) {
    return apiClient.put('/ai-engine/settings/', data)
  },

  rebuildIndexes(providerId, indexNames = []) {
    return apiClient.post('/ai-engine/settings/rebuild-indexes/', {
      provider_id: providerId,
      index_names: indexNames,
    })
  },
}
