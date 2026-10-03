import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'
import { modelAPI } from '../api/model'
import { useUserStore } from './user'
import { ElMessage } from 'element-plus'
import { logger } from '../utils/logger'
import { extractErrorMessage } from '../utils/apiErrorHandler'
import { initSpecialParams, isThinkingEnabled, applySpecialParamChange } from '../utils/specialParams'

export const useModelStore = defineStore('model', () => {
  const providers = ref([])
  const isLoading = ref(false)
  const isTesting = ref(false)
  const currentProviderId = ref('openai')
  const currentModelName = ref(null)
  const specialParams = ref({})
  const temperature = ref(0.7)
  const maxTokens = ref(10240)
  const testResult = ref(null)

  const currentProvider = computed(() => {
    return providers.value.find(p => p.id === currentProviderId.value) || null
  })

  const currentProviderSpecialParams = computed(() => {
    if (!currentProvider.value) return {}
    return currentProvider.value.specialParams || {}
  })

  const thinkingEnabled = computed(() => {
    return isThinkingEnabled(specialParams.value?.thinking)
  })

  const currentModelCapabilities = computed(() => {
    const provider = providers.value.find(p => p.id === currentProviderId.value)
    if (!provider) return []
    const model = provider.models?.find(m =>
      (typeof m === 'string' ? m === currentModelName.value : m.name === currentModelName.value)
    )
    if (!model || typeof model === 'string') return ['tool_calling', 'streaming']
    return model.capabilities || ['tool_calling', 'streaming']
  })

  const _initSpecialParams = (provider) => {
    return initSpecialParams(provider)
  }

  const loadProviders = async () => {
    isLoading.value = true
    try {
      const res = await modelAPI.getProviders()
      const data = res.data
      if (data?.code === 200 && data?.data?.providers) {
        providers.value = data.data.providers

        // 系统默认模型直接取自 providers 响应（后端从 SystemConfig.default_chat_model
        // 派生 defaultProvider/defaultModel 公开字段，经 axios 拦截器转 camelCase）。
        // 不再请求 IsAdminUser 的 /ai-engine/settings/，普通用户加载零 403。
        const savedProvider = data.data.defaultProvider || null
        const savedModel = data.data.defaultModel || null

        if (savedProvider && providers.value.find(p => p.id === savedProvider && p.available)) {
          currentProviderId.value = savedProvider
          currentModelName.value = savedModel || providers.value.find(p => p.id === savedProvider)?.defaultModel || ''
          const provider = providers.value.find(p => p.id === savedProvider)
          if (provider) specialParams.value = _initSpecialParams(provider)
        } else if (savedProvider && !providers.value.find(p => p.id === savedProvider && p.available)) {
          // 保存的默认模型已不可用，提示用户并回退到第一个可用 provider
          const savedLabel = providers.value.find(p => p.id === savedProvider)?.label || savedProvider
          const firstAvailable = providers.value.find(p => p.available)
          if (firstAvailable) {
            currentProviderId.value = firstAvailable.id
            currentModelName.value = firstAvailable.defaultModel
            specialParams.value = _initSpecialParams(firstAvailable)
            ElMessage.warning(`您配置的默认模型 ${savedLabel} 已不可用，已切换到 ${firstAvailable.label}`)
          }
        } else if (!currentProviderId.value || !providers.value.find(p => p.id === currentProviderId.value)) {
          const firstAvailable = providers.value.find(p => p.available)
          if (firstAvailable) {
            currentProviderId.value = firstAvailable.id
            currentModelName.value = firstAvailable.defaultModel
            specialParams.value = _initSpecialParams(firstAvailable)
          }
        } else if (!currentModelName.value) {
          const current = providers.value.find(p => p.id === currentProviderId.value)
          if (current) {
            currentModelName.value = current.defaultModel
            specialParams.value = _initSpecialParams(current)
          }
        }
      }
    } catch (e) {
      logger.error('加载模型列表失败:', e)
    } finally {
      isLoading.value = false
    }
  }

  const selectProvider = (providerId, modelName = null) => {
    const provider = providers.value.find(p => p.id === providerId)
    if (!provider) {
      ElMessage.warning(`未知的模型提供商: ${providerId}`)
      return
    }
    if (!provider.available) {
      ElMessage.warning(`${provider.label} 的 API Key 未配置，不可用`)
      return
    }
    currentProviderId.value = providerId
    currentModelName.value = modelName || provider.defaultModel
    specialParams.value = _initSpecialParams(provider)
    testResult.value = null
  }

  const setSpecialParam = (key, value) => {
    specialParams.value = applySpecialParamChange(
      specialParams.value,
      key,
      value,
      currentProviderSpecialParams.value
    )
  }

  const setTemperature = (val) => {
    temperature.value = val
  }

  const setMaxTokens = (val) => {
    maxTokens.value = val
  }

  const testConnection = async () => {
    if (!currentProviderId.value) return
    isTesting.value = true
    testResult.value = null
    try {
      const res = await modelAPI.testConnection(
        currentProviderId.value,
        currentModelName.value,
      )
      const data = res.data
      if (data?.code === 200) {
        testResult.value = { success: true, message: data.message }
        ElMessage.success('模型连接测试成功')
      } else {
        testResult.value = { success: false, message: data.message }
        ElMessage.error(data.message || '模型连接测试失败')
      }
    } catch (e) {
      const msg = extractErrorMessage(e)
      testResult.value = { success: false, message: msg }
      ElMessage.error(msg)
    } finally {
      isTesting.value = false
    }
  }

  const getModelConfig = () => {
    if (!currentProviderId.value) return {}
    const config = {
      providerId: currentProviderId.value,
      modelName: currentModelName.value,
      temperature: temperature.value,
      maxTokens: maxTokens.value,
      specialParams: Object.keys(specialParams.value).length > 0 ? specialParams.value : null,
    }
    return config
  }

  const clearAll = () => {
    providers.value = []
    currentProviderId.value = 'openai'
    currentModelName.value = null
    specialParams.value = {}
    temperature.value = 0.7
    maxTokens.value = 10240
    testResult.value = null
  }

  // 登出时清除模型数据，防止跨用户数据泄露
  const userStore = useUserStore()
  watch(() => userStore.isLoggedIn, (newVal) => {
    if (!newVal) {
      clearAll()
    }
  })

  return {
    providers,
    isLoading,
    isTesting,
    currentProviderId,
    currentModelName,
    specialParams,
    temperature,
    maxTokens,
    testResult,
    currentProvider,
    currentProviderSpecialParams,
    thinkingEnabled,
    currentModelCapabilities,
    loadProviders,
    selectProvider,
    setSpecialParam,
    setTemperature,
    setMaxTokens,
    testConnection,
    getModelConfig,
  }
})
