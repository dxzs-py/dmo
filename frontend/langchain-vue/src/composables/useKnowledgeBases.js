import { ref } from 'vue'
import { knowledgeAPI } from '@/api'
import { ElMessage } from 'element-plus'
import { logger } from '@/utils/logger'

/**
 * 深度研究知识库列表 composable
 *
 * 封装知识库列表的加载与搜索过滤：
 *   - refreshKnowledgeBases：从后端拉取知识库列表
 *   - filterKnowledgeBases：按名称/描述过滤（kbSearchQuery 为空时返回全部）
 *
 * @returns {{
 *   knowledgeBases: import('vue').Ref<Array<Object>>,
 *   kbLoading: import('vue').Ref<boolean>,
 *   kbSearchQuery: import('vue').Ref<string>,
 *   filteredKnowledgeBases: import('vue').Ref<Array<Object>>,
 *   refreshKnowledgeBases: () => Promise<void>,
 *   filterKnowledgeBases: () => void,
 * }}
 */
export function useKnowledgeBases() {
  const knowledgeBases = ref([])
  const kbLoading = ref(false)
  const kbSearchQuery = ref('')
  const filteredKnowledgeBases = ref([])

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

  const refreshKnowledgeBases = async () => {
    kbLoading.value = true
    try {
      const response = await knowledgeAPI.getKnowledgeBases()
      const data = response.data?.data || response.data
      knowledgeBases.value = data?.items || []
      filterKnowledgeBases()
    } catch (error) {
      logger.error('加载知识库列表失败:', error)
      ElMessage.error('加载知识库列表失败')
    } finally {
      kbLoading.value = false
    }
  }

  return {
    knowledgeBases,
    kbLoading,
    kbSearchQuery,
    filteredKnowledgeBases,
    refreshKnowledgeBases,
    filterKnowledgeBases,
  }
}
