import { ref, computed } from 'vue'
import { modelAPI } from '../api/model'
import { ElMessage } from 'element-plus'
import { extractErrorMessage } from '../utils/apiErrorHandler'
import { initSpecialParams, isThinkingEnabled, applySpecialParamChange } from '../utils/specialParams'

/**
 * 模型选择状态工厂（spec：model.js vs researchSettings.js ~70 行双实现收敛）
 *
 * model store（聊天模块全局）与 researchSettings store（深度研究独立状态）需要
 * "同结构、状态独立"的两份模型选择状态——结构层（选中态/特殊参数/能力推断/
 * 连接测试/参数组装）由本工厂唯一承载，providers 数据源由调用方注入：
 * - model store：自持 providers（loadProviders 是其职责）
 * - researchSettings：只读共享 modelStore.providers（根因 C 解耦，见其文件头）
 *
 * 状态操作语义（selectProvider 可用性校验 / testConnection 错误提取 /
 * getModelConfig 组装）以原 model store 实现为权威；researchSettings 迁移后
 * 仅一处语义统一：selectProvider 时同样清空 testResult（切换后旧结果作废）。
 */
export function createModelState({ getProviders }) {
  const currentProviderId = ref('openai')
  const currentModelName = ref(null)
  const specialParams = ref({})
  const temperature = ref(0.7)
  const maxTokens = ref(10240)
  const isTesting = ref(false)
  const testResult = ref(null)

  const currentProvider = computed(() => {
    return getProviders().find(p => p.id === currentProviderId.value) || null
  })

  const currentProviderSpecialParams = computed(() => {
    if (!currentProvider.value) return {}
    return currentProvider.value.specialParams || {}
  })

  const thinkingEnabled = computed(() => {
    return isThinkingEnabled(specialParams.value?.thinking)
  })

  const currentModelCapabilities = computed(() => {
    const provider = getProviders().find(p => p.id === currentProviderId.value)
    if (!provider) return []
    const model = provider.models?.find(m =>
      (typeof m === 'string' ? m === currentModelName.value : m.name === currentModelName.value)
    )
    if (!model || typeof model === 'string') return ['tool_calling', 'streaming']
    return model.capabilities || ['tool_calling', 'streaming']
  })

  const selectProvider = (providerId, modelName = null) => {
    const provider = getProviders().find(p => p.id === providerId)
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

  return {
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
  }
}
