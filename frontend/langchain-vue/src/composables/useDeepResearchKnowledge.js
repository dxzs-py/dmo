import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { knowledgeAPI } from '@/api/knowledge'
import { deepResearchAPI } from '@/api/research'
import { useApiTask } from '@/composables/useApiTask'
import { logger } from '@/utils/logger'

/**
 * 深度研究知识库 composable（实例安全）
 *
 * 职责：
 *   1. 知识库列表加载与关键字过滤（主表单 / 续研对话框共用数据源）
 *   2. 深度研究任务的文档分析文件查找与内容加载
 *
 * 实例安全约束：全部 ref 均为调用实例私有，无模块级可变状态；
 * 异步任务经 useApiTask 托管（代际 token + onScopeDispose 卸载作废），
 * 组件卸载后 pending 结果丢弃、loading 复位，无手动定时器需要清理。
 *
 * @param {object} deps
 * @param {import('vue').ComputedRef<object|null>} deps.task - 当前任务（响应式）
 */
export function useDeepResearchKnowledge({ task }) {
  /** 全量知识库列表（内部中间状态，过滤结果对外暴露） */
  const knowledgeBases = ref([])
  const kbSearchQuery = ref('')
  const filteredKnowledgeBases = ref([])

  const docAnalysisContent = ref(null)
  const docAnalysisFile = ref(null)

  const {
    run: runRefreshKnowledgeBases,
    loading: kbLoading,
  } = useApiTask(
    () => knowledgeAPI.getKnowledgeBases(),
    {
      showErrorToast: false,
      onSuccess: (response) => {
        const data = response.data?.data || response.data
        knowledgeBases.value = data?.items || []
        filterKnowledgeBases()
      },
      onError: (error) => {
        logger.error('加载知识库列表失败:', error)
        ElMessage.error('加载知识库列表失败')
      },
    }
  )

  const refreshKnowledgeBases = () => runRefreshKnowledgeBases()

  const filterKnowledgeBases = () => {
    const query = kbSearchQuery.value.toLowerCase().trim()
    if (!query) {
      filteredKnowledgeBases.value = [...knowledgeBases.value]
    } else {
      filteredKnowledgeBases.value = knowledgeBases.value.filter(
        kb => kb.name?.toLowerCase().includes(query) || kb.description?.toLowerCase().includes(query)
      )
    }
  }

  // 查找任务的分析文件（失败返回 undefined，调用方按 falsy 判断兼容原 null 语义）
  const {
    run: runFindDocAnalysisFile,
  } = useApiTask(
    async () => {
      if (!task.value?.taskId) return null
      const res = await deepResearchAPI.getFiles(task.value.taskId)
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
      if (mdFiles.length > 0) return mdFiles[0].relativePath || mdFiles[0].name
      const rootNotes = files.filter(f => f.type === 'file' && f.name?.endsWith('.txt'))
      if (rootNotes.length > 0) return rootNotes[0].relativePath || rootNotes[0].name
      return null
    },
    {
      loading: false,
      showErrorToast: false,
    }
  )

  const {
    run: runLoadDocAnalysis,
    loading: docAnalysisLoading,
  } = useApiTask(
    async () => {
      const file = await runFindDocAnalysisFile()
      if (file) {
        const response = await deepResearchAPI.getFileContent(task.value.taskId, file)
        const data = response.data?.data || response.data
        docAnalysisContent.value = data?.content || data || ''
        docAnalysisFile.value = file
      } else {
        docAnalysisContent.value = null
      }
    },
    {
      showErrorToast: false,
      onError: (error) => {
        logger.warn('加载文档分析详情失败:', error)
        docAnalysisContent.value = null
      },
    }
  )

  const loadDocAnalysis = () => {
    if (!task.value?.taskId || !task.value?.knowledgeBaseIds?.length) return
    return runLoadDocAnalysis()
  }

  const checkDocAnalysisFile = () => {
    docAnalysisFile.value = null
    docAnalysisContent.value = null
  }

  const autoLoadDocAnalysis = async () => {
    if (!task.value?.taskId) return
    if (!task.value?.enableDocAnalysis) return
    await loadDocAnalysis()
  }

  return {
    kbSearchQuery,
    filteredKnowledgeBases,
    kbLoading,
    refreshKnowledgeBases,
    filterKnowledgeBases,
    docAnalysisContent,
    docAnalysisFile,
    docAnalysisLoading,
    loadDocAnalysis,
    checkDocAnalysisFile,
    autoLoadDocAnalysis,
  }
}
