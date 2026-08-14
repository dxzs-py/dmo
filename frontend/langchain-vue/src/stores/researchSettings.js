import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'
import { useModelStore } from './model'
import { modelAPI } from '../api/model'
import { ElMessage } from 'element-plus'
import { initSpecialParams, isThinkingEnabled, applySpecialParamChange } from '../utils/specialParams'

/**
 * 深度研究模块独立模型设置 store（根因 C 解耦）
 *
 * 与 modelStore（聊天模块全局设置）完全独立：
 * - 深度研究页的模型选择、深度思考开关、温度/Token 参数仅影响本 store，
 *   不干扰聊天模块（含聊天深度研究模式）的全局 modelStore 状态
 * - providers 列表为只读共享（唯一数据源由 modelStore.loadProviders 加载）
 * - 首次进入时从 modelStore 拷贝当前配置作为初始值，之后互不影响
 */
export const useResearchSettingsStore = defineStore('researchSettings', () => {
  const modelStore = useModelStore()

  const currentProviderId = ref('openai')
  const currentModelName = ref(null)
  const specialParams = ref({})
  const temperature = ref(0.7)
  const maxTokens = ref(10240)
  const isTesting = ref(false)
  const testResult = ref(null)

  // providers 列表只读共享（唯一数据源由 modelStore.loadProviders 加载）
  const providers = computed(() => modelStore.providers)

  const currentProvider = computed(() => {
    return modelStore.providers.find(p => p.id === currentProviderId.value) || null
  })

  const currentProviderSpecialParams = computed(() => {
    if (!currentProvider.value) return {}
    return currentProvider.value.specialParams || {}
  })

  const thinkingEnabled = computed(() => {
    return isThinkingEnabled(specialParams.value?.thinking)
  })

  const currentModelCapabilities = computed(() => {
    const provider = modelStore.providers.find(p => p.id === currentProviderId.value)
    if (!provider) return []
    const model = provider.models?.find(m =>
      (typeof m === 'string' ? m === currentModelName.value : m.name === currentModelName.value)
    )
    if (!model || typeof model === 'string') return ['tool_calling', 'streaming']
    return model.capabilities || ['tool_calling', 'streaming']
  })

  // 首次 providers 加载完成后，从 modelStore 拷贝当前配置作为初始值。
  // ⚠️ 不能仅监听 providers：loadProviders 中 providers.value 先赋值，
  // currentProviderId/currentModelName 在 await getAISettings 之后才更新，
  // 过早同步会把全局默认值('openai')当成最终值，且 initialized 置位后不再纠正。
  // 因此同时监听 isLoading（加载完成前不同步），确保拿到最终模型配置。
  let initialized = false
  const syncFromModelStore = () => {
    if (initialized) return
    if (modelStore.isLoading) return
    if (modelStore.providers.length === 0) return
    initialized = true
    currentProviderId.value = modelStore.currentProviderId
    currentModelName.value = modelStore.currentModelName
    specialParams.value = { ...modelStore.specialParams }
    temperature.value = modelStore.temperature
    maxTokens.value = modelStore.maxTokens
  }
  watch(
    () => [modelStore.providers, modelStore.isLoading],
    () => syncFromModelStore(),
    { immediate: true }
  )

  const selectProvider = (providerId, modelName = null) => {
    const provider = modelStore.providers.find(p => p.id === providerId)
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
    specialParams.value = initSpecialParams(provider)
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

  const getModelConfig = () => {
    if (!currentProviderId.value) return {}
    return {
      providerId: currentProviderId.value,
      modelName: currentModelName.value,
      temperature: temperature.value,
      maxTokens: maxTokens.value,
      specialParams: Object.keys(specialParams.value).length > 0 ? specialParams.value : null,
    }
  }

  const testConnection = async () => {
    if (!currentProviderId.value) return
    isTesting.value = true
    testResult.value = null
    try {
      const res = await modelAPI.testConnection(currentProviderId.value, currentModelName.value)
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

  return {
    currentProviderId,
    currentModelName,
    specialParams,
    temperature,
    maxTokens,
    isTesting,
    testResult,
    providers,
    currentProvider,
    currentProviderSpecialParams,
    thinkingEnabled,
    currentModelCapabilities,
    selectProvider,
    setSpecialParam,
    setTemperature,
    setMaxTokens,
    getModelConfig,
    testConnection,
    syncFromModelStore,
  }
})
