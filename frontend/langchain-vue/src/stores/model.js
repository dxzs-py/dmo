import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'
import { modelAPI } from '../api/model'
import { useUserStore } from './user'
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
  const maxTokens = ref(10240)
  const testResult = ref(null)
  const switchResult = ref(null)

  const currentProvider = computed(() => {
    return providers.value.find(p => p.id === currentProviderId.value) || null
  })

  const currentProviderSpecialParams = computed(() => {
    if (!currentProvider.value) return {}
    return currentProvider.value.specialParams || {}
  })

  const selectedModelLabel = computed(() => {
    if (!currentProvider.value) return ''
    const modelName = currentModelName.value || currentProvider.value.defaultModel
    return `${currentProvider.value.label} / ${modelName}`
  })

  const thinkingEnabled = computed(() => {
    const thinking = specialParams.value?.thinking
    if (!thinking) return false
    // DeepSeek/Anthropic 格式: { type: "enabled" }
    if (typeof thinking === 'object' && thinking.type === 'enabled') return true
    // Ollama 格式: true
    if (thinking === true) return true
    return false
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
    const spConfig = provider?.specialParams
    if (!spConfig || typeof spConfig !== 'object') return {}
    const init = {}
    for (const [key, cfg] of Object.entries(spConfig)) {
      if (cfg.type === 'toggle' && cfg.default === true) {
        init[key] = cfg.enabled_value
      } else if (cfg.type === 'select' && cfg.default) {
        init[key] = cfg.default
      }
    }
    // DeepSeek: reasoning_effort 仅在 thinking 已启用时才生效
    // 如果 thinking 未启用（default=false），移除 reasoning_effort 避免强制启用 thinking
    if ('reasoning_effort' in init && 'thinking' in spConfig && !('thinking' in init)) {
      delete init.reasoning_effort
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

        // 尝试从数据库读取用户保存的默认模型
        let savedProvider = null
        let savedModel = null
        try {
          const settingsRes = await modelAPI.getAISettings()
          const settingsData = settingsRes.data?.data
          if (settingsData?.current?.defaultChatModel?.providerId) {
            savedProvider = settingsData.current.defaultChatModel.providerId
            savedModel = settingsData.current.defaultChatModel.modelName
          }
        } catch {
          // 忽略，使用默认逻辑
        }

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
    switchResult.value = null
  }

  const setSpecialParam = (key, value) => {
    if (value === undefined || value === null) {
      const newParams = { ...specialParams.value }
      delete newParams[key]
      specialParams.value = newParams
    } else {
      specialParams.value = { ...specialParams.value, [key]: value }
    }
    // DeepSeek: 关闭 thinking 时同步移除 reasoning_effort（API 约束）
    const spConfig = currentProviderSpecialParams.value
    if (key === 'thinking' && spConfig?.reasoning_effort) {
      const isThinkingOff = !value || (typeof value === 'object' && value.type !== 'enabled') || value === false
      if (isThinkingOff && 'reasoning_effort' in specialParams.value) {
        const newParams = { ...specialParams.value }
        delete newParams.reasoning_effort
        specialParams.value = newParams
      }
    }
    // DeepSeek: 设置 reasoning_effort 时自动启用 thinking（API 约束）
    if (key === 'reasoning_effort' && spConfig?.thinking) {
      const thinkingAlreadyEnabled = specialParams.value.thinking?.type === 'enabled'
      if (!thinkingAlreadyEnabled) {
        specialParams.value = { ...specialParams.value, thinking: spConfig.thinking.enabled_value }
      }
    }
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
        config.providerId,
        config.modelName,
        {
          temperature: config.temperature,
          maxTokens: config.maxTokens,
          specialParams: config.specialParams,
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
    switchResult.value = null
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
    currentModelCapabilities,
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
