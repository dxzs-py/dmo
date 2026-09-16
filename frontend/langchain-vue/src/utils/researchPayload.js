/**
 * 深度研究任务发起请求负载构造（Spec: 沙箱执行加固 / 任务级开关 3.1）
 *
 * 纯函数：便于单测"沙箱开关默认值（false）"与"enableSandbox 透传"。
 * 请求 body 键为前端 camelCase，由 axios 请求拦截器 toSnakeCase 统一转为
 * 网络契约键 enable_sandbox（网络边界 snake_case）。
 */

/**
 * 构造 deepResearchAPI.start 请求负载
 * @param {Object} params - 构造入参
 * @param {Object} params.form - DeepResearchView 的 researchForm（含 query/enableWebSearch/
 *   knowledgeBaseIds/useMcp/selectedMcpServers/selectedTools/providerId/modelName/enableSandbox）
 * @param {Object} params.modelConfig - researchSettings.getModelConfig() 结果
 * @param {boolean} params.enableDeepThinking - 深度思考开关（researchSettings.thinkingEnabled）
 * @returns {Object} start 请求负载（camelCase，拦截器转 snake_case）
 */
export function buildResearchStartPayload({ form, modelConfig, enableDeepThinking }) {
  return {
    query: form.query,
    enableWebSearch: form.enableWebSearch,
    enableDocAnalysis: form.knowledgeBaseIds.length > 0,
    knowledgeBaseIds: form.knowledgeBaseIds,
    useMcp: form.useMcp,
    selectedMcpServers: form.selectedMcpServers,
    selectedTools: form.selectedTools,
    providerId: form.providerId || modelConfig.providerId,
    modelName: form.modelName || modelConfig.modelName,
    enableDeepThinking,
    temperature: modelConfig.temperature,
    maxTokens: modelConfig.maxTokens,
    specialParams: modelConfig.specialParams,
    // 沙箱任务级开关（默认 false；拦截器转 enable_sandbox）
    enableSandbox: form.enableSandbox === true,
  }
}
