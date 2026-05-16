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
}
