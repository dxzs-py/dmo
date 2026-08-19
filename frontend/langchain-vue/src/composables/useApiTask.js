import { ref, getCurrentScope, onScopeDispose } from 'vue'
import { ElMessage } from 'element-plus'
import { extractErrorMessage } from '@/utils/apiErrorHandler'

/** 手动取消类错误名（AbortController abort / 主动取消约定），不视为业务失败 */
const CANCEL_ERROR_NAMES = ['AbortError', 'CancellationError']

/**
 * 判断是否为手动取消类错误（AbortController abort / 主动取消）
 * @param {unknown} err - 捕获的异常
 * @returns {boolean}
 */
function isCancelError(err) {
  return !!err && typeof err === 'object' && CANCEL_ERROR_NAMES.includes(err.name)
}

/**
 * 异步任务托管 composable
 *
 * 职责（视图层异步调用点迁移的统一底座）：
 *   1. loading / error 状态托管（均为 ref，可直接绑定模板）
 *   2. 重复调用防护：新 run 触发时旧任务作废（代际 token 机制），
 *      旧任务 resolve/reject 后丢弃结果——不弹 toast、不置 error、不动 loading
 *   3. 组件卸载自动清理：onScopeDispose 置 disposed 标志，
 *      pending 任务结果全部丢弃、loading 复位、不弹任何 toast；
 *      disposed 后再次 run 直接静默跳过（不执行 taskFn）
 *   4. 错误分类：手动取消类错误（err.name 为 AbortError / CancellationError）
 *      不弹 toast、不置 error、不触发 onError
 *
 * run 的返回值语义：成功返回 taskFn 的 resolve 值；
 * 失败 / 手动取消 / 任务作废（被新 run 顶替或已 dispose）一律返回 undefined，
 * 失败细节通过 error ref 与 onError 回调获取，run 本身不 rethrow
 * （避免调用方遗漏 catch 造成 unhandled rejection）。
 *
 * @param {(...args: any[]) => Promise} taskFn - 异步任务函数，run(args) 的参数原样透传
 * @param {object} [options]
 * @param {boolean} [options.loading=true] - 是否托管 loading 状态
 * @param {boolean} [options.showErrorToast=true] - 异常时是否弹 ElMessage.error
 * @param {boolean} [options.showSuccessToast=false] - 成功时是否弹 ElMessage.success
 * @param {boolean} [options.autoAbortOnUnmount=true] - 卸载时是否自动作废 pending 任务
 * @param {(err: unknown) => void} [options.onError] - 失败回调（取消/作废不触发）
 * @param {(data: any) => void} [options.onSuccess] - 成功回调（作废不触发）
 * @returns {{
 *   run: (...args: any[]) => Promise<any>,
 *   loading: import('vue').Ref<boolean>,
 *   error: import('vue').Ref<any>,
 * }}
 */
export function useApiTask(taskFn, options = {}) {
  const {
    loading: manageLoading = true,
    showErrorToast = true,
    showSuccessToast = false,
    autoAbortOnUnmount = true,
    onError,
    onSuccess,
  } = options

  /** @type {import('vue').Ref<boolean>} */
  const loading = ref(false)
  /** @type {import('vue').Ref<any>} */
  const error = ref(null)

  /** 代际 token：每次 run 自增，旧的在途任务持有旧 token 即作废 */
  let generation = 0
  /** 组件卸载（scope dispose）后置位，之后所有任务结果丢弃 */
  let disposed = false

  if (autoAbortOnUnmount && getCurrentScope()) {
    onScopeDispose(() => {
      disposed = true
      if (manageLoading) loading.value = false
    })
  }

  const run = async (...args) => {
    // 已卸载：不再发起新任务，静默跳过
    if (disposed) return undefined

    const token = ++generation
    if (manageLoading) loading.value = true
    // 新任务开始，清除上一轮的过期错误
    error.value = null

    try {
      const data = await taskFn(...args)
      // 作废检查：已被新 run 顶替或组件已卸载 → 丢弃结果（不动 loading、不弹 toast）
      if (disposed || token !== generation) return undefined

      if (manageLoading) loading.value = false
      if (showSuccessToast) ElMessage.success('操作成功')
      if (onSuccess) onSuccess(data)
      return data
    } catch (err) {
      // 作废检查：旧任务的失败同样丢弃，不覆盖新任务的 loading、不弹 toast
      if (disposed || token !== generation) return undefined

      if (manageLoading) loading.value = false
      // 手动取消：不弹 toast、不置 error、不触发 onError
      if (isCancelError(err)) return undefined

      error.value = err
      if (showErrorToast) ElMessage.error(extractErrorMessage(err))
      if (onError) onError(err)
      return undefined
    }
  }

  return { run, loading, error }
}
