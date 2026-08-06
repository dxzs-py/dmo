/**
 * 模型特殊参数（specialParams）共享工具
 *
 * 由 modelStore（聊天模块全局设置）与 researchSettingsStore
 * （深度研究模块独立设置）共用，避免两处重复定义相同逻辑。
 */

/**
 * 根据 provider 的 specialParams 配置生成初始参数值
 * @param {object|null} provider - provider 配置（含 specialParams 字段）
 * @returns {object} 初始参数对象
 */
export function initSpecialParams(provider) {
  const spConfig = provider?.specialParams
  if (!spConfig || typeof spConfig !== 'object') return {}
  const init = {}
  for (const [key, cfg] of Object.entries(spConfig)) {
    if (cfg.type === 'toggle' && cfg.default === true) {
      init[key] = cfg.enabledValue
    } else if (cfg.type === 'select' && cfg.default) {
      init[key] = cfg.default
    }
  }
  // DeepSeek: reasoningEffort 仅在 thinking 已启用时才生效
  // 如果 thinking 未启用（default=false），移除 reasoningEffort 避免强制启用 thinking
  if ('reasoningEffort' in init && 'thinking' in spConfig && !('thinking' in init)) {
    delete init.reasoningEffort
  }
  return init
}

/**
 * 判断 thinking 参数是否处于启用态
 * - DeepSeek/Anthropic 格式: { type: "enabled" }
 * - Ollama 格式: true
 * @param {*} thinking - specialParams.thinking 的值
 * @returns {boolean}
 */
export function isThinkingEnabled(thinking) {
  if (!thinking) return false
  if (typeof thinking === 'object' && thinking.type === 'enabled') return true
  if (thinking === true) return true
  return false
}

/**
 * 应用 specialParams 中单个 key 的变更（含 reasoningEffort 联动约束）
 * @param {object} current - 当前参数对象
 * @param {string} key - 变更的键
 * @param {*} value - 新值（null/undefined 表示删除）
 * @param {object|undefined} spConfig - provider.specialParams 配置
 * @returns {object} 变更后的新参数对象（不修改入参）
 */
export function applySpecialParamChange(current, key, value, spConfig) {
  const next = { ...current }
  if (value === undefined || value === null) {
    delete next[key]
  } else {
    next[key] = value
  }
  // DeepSeek: 关闭 thinking 时同步移除 reasoningEffort（API 约束）
  if (key === 'thinking' && spConfig?.reasoningEffort) {
    const isThinkingOff = !value || (typeof value === 'object' && value.type !== 'enabled') || value === false
    if (isThinkingOff && 'reasoningEffort' in next) {
      delete next.reasoningEffort
    }
  }
  // DeepSeek: 设置 reasoningEffort 时自动启用 thinking（API 约束）
  if (key === 'reasoningEffort' && spConfig?.thinking) {
    const thinkingAlreadyEnabled = next.thinking?.type === 'enabled'
    if (!thinkingAlreadyEnabled) {
      next.thinking = spConfig.thinking.enabledValue
    }
  }
  return next
}
