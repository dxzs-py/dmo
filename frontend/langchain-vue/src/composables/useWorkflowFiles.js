import { ref } from 'vue'
import { workflowAPI } from '@/api/workflow'
import { logger } from '@/utils/logger'

/**
 * 学习工作流文件处理 composable
 *
 * 封装工作流生成文件的"关键文件"自动查找与加载逻辑：
 *   - _findKeyFile：在文件树中优先查找 notes/ 目录下的 .md/.txt，其次根目录非 report 的 .md，再次根目录 .txt
 *   - autoLoadKeyFile：查找关键文件并读取其内容，写入 autoLoadContent（供"学习笔记"区展示）
 *
 * @param {Object} deps
 * @param {import('vue').Ref<Object|null>} deps.execution - 当前工作流执行状态 ref（读取 thread_id）
 * @returns {{
 *   autoLoadContent: import('vue').Ref<string|null>,
 *   autoLoadLoading: import('vue').Ref<boolean>,
 *   _findKeyFile: () => Promise<string|null>,
 *   autoLoadKeyFile: () => Promise<void>,
 *   clearAutoLoad: () => void,
 * }}
 */
export function useWorkflowFiles({ execution }) {
  const autoLoadContent = ref(null)
  const autoLoadLoading = ref(false)

  /** 清空已加载的学习资料内容（切换任务 / 重置工作流时调用） */
  const clearAutoLoad = () => {
    autoLoadContent.value = null
  }

  /**
   * 在工作流文件树中查找"关键学习资料"文件
   * 优先级：notes/ 目录下的 .md > notes/ 下的 .txt > 根目录非 report 的 .md > 根目录 .txt
   * @returns {Promise<string|null>} 文件相对路径，未找到返回 null
   */
  const _findKeyFile = async () => {
    if (!execution.value?.threadId) return null
    try {
      const res = await workflowAPI.getFiles(execution.value.threadId)
      const data = res.data?.data || res.data
      const files = data?.files || data || []
      // 优先查找 notes/ 目录下的 .md 文件
      const notesDir = files.find(f => f.name === 'notes' && f.type === 'directory')
      if (notesDir && notesDir.children) {
        const mdFile = notesDir.children.find(f => f.name?.endsWith('.md'))
        if (mdFile) return `notes/${mdFile.name}`
        const txtFile = notesDir.children.find(f => f.name?.endsWith('.txt'))
        if (txtFile) return `notes/${txtFile.name}`
      }
      // 查找根目录下非 report 的 .md 文件
      const mdFiles = files.filter(f => f.type === 'file' && f.name?.endsWith('.md') && !f.name?.includes('report'))
      if (mdFiles.length > 0) return mdFiles[0].relativePath || mdFiles[0].name
      // 查找根目录下的 .txt 文件
      const rootNotes = files.filter(f => f.type === 'file' && f.name?.endsWith('.txt'))
      if (rootNotes.length > 0) return rootNotes[0].relativePath || rootNotes[0].name
    } catch {
      // 查找关键文件失败时返回 null
    }
    return null
  }

  /**
   * 自动加载关键学习资料文件内容
   * 查找失败或读取失败时静默处理（仅 logger.warn），不抛错
   * @returns {Promise<void>}
   */
  const autoLoadKeyFile = async () => {
    if (!execution.value?.threadId) return
    autoLoadContent.value = null
    autoLoadLoading.value = true
    try {
      const file = await _findKeyFile()
      if (file) {
        const response = await workflowAPI.getFileContent(execution.value.threadId, file)
        const data = response.data?.data || response.data
        autoLoadContent.value = data?.content || data || ''
      }
    } catch (error) {
      logger.warn('自动加载学习资料失败:', error)
    } finally {
      autoLoadLoading.value = false
    }
  }

  return {
    autoLoadContent,
    autoLoadLoading,
    _findKeyFile,
    autoLoadKeyFile,
    clearAutoLoad,
  }
}

