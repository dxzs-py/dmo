import { defineStore } from 'pinia'
import { computed, watch } from 'vue'
import { useModelStore } from './model'
import { createModelState } from './createModelState'

/**
 * 深度研究模块独立模型设置 store（根因 C 解耦）
 *
 * 与 modelStore（聊天模块全局设置）完全独立：
 * - 深度研究页的模型选择、深度思考开关、温度/Token 参数仅影响本 store，
 *   不干扰聊天模块（含聊天深度研究模式）的全局 modelStore 状态
 * - providers 列表为只读共享（唯一数据源由 modelStore.loadProviders 加载）
 * - 首次进入时从 modelStore 拷贝当前配置作为初始值，之后互不影响
 * - 状态结构/操作由 createModelState 工厂唯一承载（与 model store 同构复用）
 */
export const useResearchSettingsStore = defineStore('researchSettings', () => {
  const modelStore = useModelStore()

  // providers 列表只读共享（唯一数据源由 modelStore.loadProviders 加载）
  const providers = computed(() => modelStore.providers)

  const modelState = createModelState({ getProviders: () => modelStore.providers })
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
    getModelConfig,
    testConnection,
  } = modelState

  // 首次 providers 加载完成后，从 modelStore 拷贝当前配置作为初始值。
  // ⚠️ 不能仅监听 providers：loadProviders 中 providers.value 先赋值，
  // currentProviderId/currentModelName 在系统默认标记解析后才更新，
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
