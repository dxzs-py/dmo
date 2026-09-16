import { ref } from 'vue'
import { modelAPI } from '@/api/model'
import { logger } from '@/utils/logger'

/**
 * 沙箱全局可用性 composable（Spec: 沙箱执行加固 / 任务级开关 3.3）
 *
 * 后端沙箱可用性端点（GET /ai-engine/sandbox-availability/，普通用户可读）返回
 * `sandbox_enabled`（当前后端 SANDBOX_ENABLED=false）。本 composable 轻量拉取该标志：
 * - `sandboxEnabled === false`（含拉取失败 / 接口缺省 / 网络异常）→ 前端沙箱开关置灰，
 *   提示"沙箱模式不可用（后端未启用）"，但任务仍可正常发起（后端按本机执行）；
 * - 拉取失败默认按 false 处理（fail-safe：宁可置灰也不误导用户以为已隔离）。
 *
 * 响应经 axios 拦截器 toCamelCase 转换，此处读取 `data.sandboxEnabled`。
 * 注意：不使用管理员专属的 AI 设置接口（AISettingsView, IsAdmin），避免普通用户 403。
 */
export function useSandboxAvailability() {
  /** 后端沙箱全局开关（false = 未启用，前端沙箱开关置灰） */
  const sandboxEnabled = ref(false)
  /** 状态拉取中 */
  const sandboxStatusLoading = ref(false)

  /**
   * 拉取后端沙箱启用状态（幂等：已为 true 时不再重复请求）
   * @returns {Promise<boolean>} 拉取后的 sandboxEnabled 值
   */
  const loadSandboxStatus = async () => {
    if (sandboxEnabled.value) return sandboxEnabled.value
    sandboxStatusLoading.value = true
    try {
      const res = await modelAPI.getSandboxAvailability()
      const data = res.data?.data
      sandboxEnabled.value = data?.sandboxEnabled === true
    } catch (e) {
      // 网络异常 / 后端字段未就绪：按未启用处理（fail-safe 置灰）
      logger.warn('[Sandbox] 拉取沙箱启用状态失败，按未启用处理:', e?.message || e)
      sandboxEnabled.value = false
    } finally {
      sandboxStatusLoading.value = false
    }
    return sandboxEnabled.value
  }

  return {
    sandboxEnabled,
    sandboxStatusLoading,
    loadSandboxStatus,
  }
}
