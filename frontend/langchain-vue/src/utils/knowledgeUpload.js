import { ElMessage, ElNotification } from 'element-plus'
import { extractErrorMessage } from './apiErrorHandler'

/**
 * 知识库文档上传公共逻辑
 *
 * 知识库管理模块（KnowledgeBaseView）与 RAG 知识库模块（RagView）共用同一套
 * 上传流程：构造 FormData → 调用后端 upload 接口 → 订阅任务进度 → 终态处理。
 * 后端两个端点（/knowledge/knowledge-bases/{id}/upload/ 与
 * /knowledge/indices/{name}/upload/）指向同一视图（异步 Celery 任务），
 * 此处统一前端行为：WebSocket task 频道实时进度，终态后刷新。
 */

/**
 * 构造上传 FormData（字段名固定为 files，与后端 getlist("files") 对齐）
 * @param {Array<{raw: File}>} files 已选择的文件列表（el-upload 的 fileList）
 * @returns {FormData}
 */
export function buildUploadFormData(files) {
  const formData = new FormData()
  files.forEach((file) => {
    formData.append('files', file.raw)
  })
  return formData
}

/**
 * 上传成功后的 Embedding 降级提示（两端行为一致）
 * @param {object|null|undefined} resultData 上传接口返回的 data
 */
export function notifyEmbeddingFallback(resultData) {
  const events = resultData?.fallbackInfo?.events
  if (events?.length) {
    const chain = events.map(e => e.fromLabel).concat([events[events.length - 1].toLabel]).join(' → ')
    ElNotification({
      title: 'Embedding 模型降级提示',
      message: `降级链路: ${chain}`,
      type: 'warning',
      duration: 8000,
    })
  }
}

/**
 * 提取上传失败的可读错误信息（统一展示后端 message，而非通用文案）
 * @param {unknown} error axios 错误对象
 * @param {string} fallback 无法解析时的兜底文案
 * @returns {string}
 */
export function getUploadErrorMessage(error, fallback = '上传失败，请检查文件格式或重试') {
  const message = extractErrorMessage(error)
  if (message && message !== '未知错误，请稍后重试') {
    return message
  }
  return fallback
}

/**
 * 跟踪知识库上传任务：订阅 WebSocket task 频道接收实时进度。
 *
 * 上传接口返回 task_id 后调用，订阅 task:{task_id} 频道（task_progress 事件）：
 * - status=started/progress → onProgress 更新进度
 * - status=success → onSuccess（result 为任务结果）
 * - status=failure → onFailure（error 为失败原因）
 *
 * 事件经 useRealtimeSync 的 replay 机制保证不丢失（订阅时携带 lastSeq，
 * 已完成的历史事件会回放），因此无需轮询兜底。
 *
 * @param {object} realtimeSync useRealtimeSync() 返回值（含 subscribeTask）
 * @param {string} taskId 上传接口返回的业务任务 ID
 * @param {object} handlers 事件回调
 * @param {Function} [handlers.onProgress] (progress: number, currentStep: string) => void
 * @param {Function} [handlers.onSuccess] (result: object|null) => void
 * @param {Function} [handlers.onFailure] (error: string) => void
 * @returns {() => void} 取消订阅函数（组件卸载时调用）
 */
export function trackUploadTask(realtimeSync, taskId, handlers) {
  if (!taskId) return () => {}
  let finished = false

  const unsubscribe = realtimeSync.subscribeTask(taskId, (event) => {
    if (event.type !== 'task_progress') return
    const data = event.payload || {}
    const { status, progress, currentStep, result, error } = data
    if (status === 'success') {
      if (finished) return
      finished = true
      unsubscribe()
      handlers.onSuccess?.(result || null)
    } else if (status === 'failure') {
      if (finished) return
      finished = true
      unsubscribe()
      handlers.onFailure?.(error || '上传处理失败')
    } else {
      handlers.onProgress?.(progress ?? 0, currentStep || '')
    }
  })

  return () => {
    if (!finished) {
      finished = true
      unsubscribe()
    }
  }
}

export { ElMessage }
