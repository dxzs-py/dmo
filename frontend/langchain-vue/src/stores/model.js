import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'
import { modelAPI } from '../api/model'
import { useUserStore } from './user'
import { logger } from '../utils/logger'
import { ElMessage } from 'element-plus'
import { initSpecialParams } from '../utils/specialParams'
import { createModelState } from './createModelState'

export const useModelStore = defineStore('model', () => {
  const providers = ref([])
  const isLoading = ref(false)

  // 结构层（选中态/特殊参数/能力推断/连接测试/参数组装）由状态工厂唯一承载
  const modelState = createModelState({ getProviders: () => providers.value })
  const {
    currentProviderId,
    currentModelName,
    specialParams,
    temperature,
    maxTokens,
    isTesting,
    testResult,
    currentProvider,
    currentProviderSpecialParams,
    thinkingEnabled,
    currentModelCapabilities,
    selectProvider,
    setSpecialParam,
    setTemperature,
    setMaxTokens,
    testConnection,
    getModelConfig,
  } = modelState

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

        if (savedProvider) {
          const saved = providers.value.find(p => p.id === savedProvider)
          const savedAvailable = saved && saved.available
          if (savedAvailable) {
            currentProviderId.value = savedProvider
            currentModelName.value = savedModel || saved?.defaultModel || ''
            if (saved) specialParams.value = _initSpecialParams(saved)
          } else {
            // 保存的默认模型已不可用，提示用户并回退到第一个可用 provider
            const savedLabel = saved?.label || savedProvider
            const firstAvailable = providers.value.find(p => p.available)
            if (firstAvailable) {
              currentProviderId.value = firstAvailable.id
              currentModelName.value = firstAvailable.defaultModel
              specialParams.value = _initSpecialParams(firstAvailable)
              ElMessage.warning(`您配置的默认模型 ${savedLabel} 已不可用，已切换到 ${firstAvailable.label}`)
            }
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
    currentProviderId,
    currentModelName,
    specialParams,
    temperature,
    maxTokens,
    isTesting,
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
