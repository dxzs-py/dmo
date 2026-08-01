import { ref } from 'vue'
import { deepResearchAPI } from '@/api'
import { logger } from '@/utils/logger'

/**
 * 深度研究文档分析 composable
 *
 * 封装研究任务关联的文档分析文件查找与内容加载逻辑：
 *   - loadDocAnalysis：根据当前任务查找分析文件并加载内容
 *   - _findDocAnalysisFile：在任务文件树中定位 notes 目录下的 .md/.txt 或根目录分析文件
 *   - checkDocAnalysisFile：重置分析文件状态（切换任务 / 任务完成时调用）
 *   - autoLoadDocAnalysis：任务启用文档分析时自动加载
 *
 * @param {Object} deps
 * @param {import('vue').Ref<Object|null>} deps.task - 当前任务 ref
 * @returns {{
 *   docAnalysisContent: import('vue').Ref<string|null>,
 *   docAnalysisLoading: import('vue').Ref<boolean>,
 *   docAnalysisFile: import('vue').Ref<string|null>,
 *   loadDocAnalysis: () => Promise<void>,
 *   checkDocAnalysisFile: () => void,
 *   autoLoadDocAnalysis: () => Promise<void>,
 * }}
 */
export function useDocAnalysis({ task }) {
  const docAnalysisContent = ref(null)
  const docAnalysisLoading = ref(false)
  const docAnalysisFile = ref(null)

  /**
   * 在任务文件树中查找文档分析文件
   * 优先级：notes 目录下的 .md → notes 目录下的 .txt → 根目录 .md（非 report） → 根目录 .txt
   * @returns {Promise<string|null>} 文件相对路径，未找到返回 null
   */
  const _findDocAnalysisFile = async () => {
    if (!task.value?.task_id) return null
    try {
      const res = await deepResearchAPI.getFiles(task.value.task_id)
      const data = res.data?.data || res.data
      const files = data?.files || data || []
      const notesDir = files.find(f => f.name === 'notes' && f.type === 'directory')
      if (notesDir && notesDir.children) {
        const mdFile = notesDir.children.find(f => f.name?.endsWith('.md'))
        if (mdFile) return `notes/${mdFile.name}`
        const txtFile = notesDir.children.find(f => f.name?.endsWith('.txt'))
        if (txtFile) return `notes/${txtFile.name}`
      }
      const mdFiles = files.filter(f => f.type === 'file' && f.name?.endsWith('.md') && !f.name?.includes('report'))
      if (mdFiles.length > 0) return mdFiles[0].relative_path || mdFiles[0].name
      const rootNotes = files.filter(f => f.type === 'file' && f.name?.endsWith('.txt'))
      if (rootNotes.length > 0) return rootNotes[0].relative_path || rootNotes[0].name
    } catch {
      // 查找关键文件失败时返回 null
    }
    return null
  }

  /**
   * 加载文档分析内容
   * 依赖当前任务已关联知识库（knowledge_base_ids 非空）
   */
  const loadDocAnalysis = async () => {
    if (!task.value?.task_id || !task.value?.knowledge_base_ids?.length) return
    docAnalysisLoading.value = true
    try {
      const file = await _findDocAnalysisFile()
      if (file) {
        const response = await deepResearchAPI.getFileContent(task.value.task_id, file)
        const data = response.data?.data || response.data
        docAnalysisContent.value = data?.content || data || ''
        docAnalysisFile.value = file
      } else {
        docAnalysisContent.value = null
      }
    } catch (error) {
      logger.warn('加载文档分析详情失败:', error)
      docAnalysisContent.value = null
    } finally {
      docAnalysisLoading.value = false
    }
  }

  /**
   * 重置文档分析状态（切换任务或任务完成时调用）
   */
  const checkDocAnalysisFile = () => {
    docAnalysisFile.value = null
    docAnalysisContent.value = null
  }

  /**
   * 任务启用文档分析时自动加载
   */
  const autoLoadDocAnalysis = async () => {
    if (!task.value?.task_id) return
    if (!task.value?.enable_doc_analysis) return
    await loadDocAnalysis()
  }

  return {
    docAnalysisContent,
    docAnalysisLoading,
    docAnalysisFile,
    loadDocAnalysis,
    checkDocAnalysisFile,
    autoLoadDocAnalysis,
  }
}
