/**
 * 沙箱开关相关纯逻辑（Spec: 沙箱执行加固 / 任务级开关）
 *
 * 聊天模块深度研究模式经 chat 请求（mode='deep-research'）创建研究任务，
 * 沙箱开关仅在深研模式下透传 enableSandbox（网络边界 snake_case）。
 * 纯函数便于单测，与 useSandboxAvailability / researchPayload.js 分工：
 * - 本模块：聊天入口的开关解析（模式 × 开关值 → 请求负载布尔值）；
 * - useSandboxAvailability：后端全局 sandbox_enabled 标志拉取（置灰依据）；
 * - researchPayload.js：深研模块 start 请求负载构造。
 */

/**
 * 解析聊天请求负载中的 enableSandbox 值
 * @param {string} mode - 当前聊天模式（'agent' / 'deep-research'）
 * @param {boolean} deepResearchSandboxEnabled - 深研沙箱任务级开关（chat store 状态）
 * @returns {boolean} 请求负载 enableSandbox 布尔值（仅深研模式且开关开启时为 true）
 */
export function resolveChatEnableSandbox(mode, deepResearchSandboxEnabled) {
  return mode === 'deep-research' && deepResearchSandboxEnabled === true
}
