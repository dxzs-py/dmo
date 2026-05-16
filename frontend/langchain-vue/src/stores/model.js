import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { modelAPI } from '../api/model'
import { ElMessage } from 'element-plus'
import { logger } from '../utils/logger'

export const useModelStore = defineStore('model', () => {
  const providers = ref([])
  const isLoading = ref(false)
  const isTesting = ref(false)
  const isSwitching = ref(false)
  const currentProviderId = ref('openai')
  const currentModelName = ref(null)
  const specialParams = ref({})
  const temperature = ref(0.7)
  const maxTokens = ref(1024)
  const testResult = ref(null)
  const switchResult = ref(null)

  const currentProvider = computed(() => {
    return providers.value.find(p => p.id === currentProviderId.value) || null
  })

  const currentProviderSpecialParams = computed(() => {
    if (!currentProvider.value) return {}
    return currentProvider.value.special_params || {}
  })

  const selectedModelLabel = computed(() => {
    if (!currentProvider.value) return ''
    const modelName = currentModelName.value || currentProvider.value.default_model
    return `${currentProvider.value.label} / ${modelName}`
  })

  const thinkingEnabled = computed(() => {
    const current = specialParams.value?.thinking
    return current?.type === 'enabled'
  })

  const _initSpecialParams = (provider) => {
    const spConfig = provider?.special_params
    if (!spConfig || typeof spConfig !== 'object') return {}
    const init = {}
    for (const [key, cfg] of Object.entries(spConfig)) {
      if (cfg.type === 'toggle' && cfg.default === true) {
        init[key] = cfg.enabled_value
      } else if (cfg.type === 'select' && cfg.default) {
        init[key] = cfg.default
      }
    }
    return init
  }

  const loadProviders = async () => {
    isLoading.value = true
    try {
      const res = await modelAPI.getProviders()
      const data = res.data
      if (data?.code === 200 && data?.data?.providers) {
        providers.value = data.data.providers
        if (!currentProviderId.value || !providers.value.find(p => p.id === currentProviderId.value)) {
          const firstAvailable = providers.value.find(p => p.available)
          if (firstAvailable) {
            currentProviderId.value = firstAvailable.id
            currentModelName.value = firstAvailable.default_model
            specialParams.value = _initSpecialParams(firstAvailable)
          }
        } else if (!currentModelName.value) {
          const current = providers.value.find(p => p.id === currentProviderId.value)
          if (current) {
            currentModelName.value = current.default_model
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
    currentModelName.value = modelName || provider.default_model
    specialParams.value = _initSpecialParams(provider)
    testResult.value = null
    switchResult.value = null
  }

  const setSpecialParam = (key, value) => {
    specialParams.value = { ...specialParams.value, [key]: value }
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
      const msg = e.response?.data?.message || e.message || '连接测试失败'
      testResult.value = { success: false, message: msg }
      ElMessage.error(msg)
    } finally {
      isTesting.value = false
    }
  }

  const switchModel = async () => {
    if (!currentProviderId.value) return false
    isSwitching.value = true
    switchResult.value = null
    try {
      const config = getModelConfig()
      const res = await modelAPI.switchModel(
        config.provider_id,
        config.model_name,
        {
          temperature: config.temperature,
          max_tokens: config.max_tokens,
          special_params: config.special_params,
        }
      )
      const data = res.data
      if (data?.code === 200) {
        switchResult.value = { success: true, message: data.message }
        ElMessage.success(data.message || '模型切换成功')
        return true
      } else {
        switchResult.value = { success: false, message: data.message }
        ElMessage.error(data.message || '模型切换失败')
        return false
      }
    } catch (e) {
      const msg = e.response?.data?.message || e.message || '模型切换失败'
      switchResult.value = { success: false, message: msg }
      ElMessage.error(msg)
      return false
    } finally {
      isSwitching.value = false
    }
  }

  const getModelConfig = () => {
    if (!currentProviderId.value) return {}
    const config = {
      provider_id: currentProviderId.value,
      model_name: currentModelName.value,
      temperature: temperature.value,
      max_tokens: maxTokens.value,
      special_params: Object.keys(specialParams.value).length > 0 ? specialParams.value : null,
    }
    return config
  }

  return {
    providers,
    isLoading,
    isTesting,
    isSwitching,
    currentProviderId,
    currentModelName,
    specialParams,
    temperature,
    maxTokens,
    testResult,
    switchResult,
    currentProvider,
    currentProviderSpecialParams,
    selectedModelLabel,
    thinkingEnabled,
    loadProviders,
    selectProvider,
    setSpecialParam,
    setTemperature,
    setMaxTokens,
    testConnection,
    switchModel,
    getModelConfig,
  }
})
