import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { toolsAPI } from '../api/tools'
import { logger } from '../utils/logger'

export const useToolsStore = defineStore('tools', () => {
  const langchainTools = ref([])
  const mcpServers = ref([])
  const skills = ref([])
  const skillPackages = ref([])
  const toolMeta = ref({ categories: [] })

  const langchainLoading = ref(false)
  const mcpLoading = ref(false)
  const skillLoading = ref(false)
  const metaLoading = ref(false)

  const langchainLoaded = ref(false)
  const mcpLoaded = ref(false)
  const skillLoaded = ref(false)
  const skillPackageLoaded = ref(false)
  const metaLoaded = ref(false)

  const systemLangchainTools = computed(() => langchainTools.value.filter(t => t.source === 'system'))
  const userLangchainTools = computed(() => langchainTools.value.filter(t => t.source === 'user'))
  const systemMcpServers = computed(() => mcpServers.value.filter(s => s.source === 'system'))
  const userMcpServers = computed(() => mcpServers.value.filter(s => s.source === 'user'))
  const systemSkills = computed(() => skills.value.filter(s => s.source === 'system'))
  const userSkills = computed(() => skills.value.filter(s => s.source === 'user'))

  const loadLangchainTools = async (force = false) => {
    if (!force && langchainLoaded.value && !langchainLoading.value) return
    langchainLoading.value = true
    try {
      const res = await toolsAPI.getToolList({ type: 'langchain' })
      const data = res.data?.data || res.data
      langchainTools.value = (data?.langchain || []).map(t => ({
        ...t,
        category: t.category || 'basic',
        visibility: t.visibility || 'selectable',
        tier: t.tier || 'standard',
      }))
      langchainLoaded.value = true
    } catch {
      langchainTools.value = []
    } finally {
      langchainLoading.value = false
    }
  }

  const loadMcpServers = async (force = false) => {
    if (!force && mcpLoaded.value && !mcpLoading.value) return
    mcpLoading.value = true
    try {
      const res = await toolsAPI.getMcpServers()
      const data = res.data?.data || res.data
      mcpServers.value = data.servers || []
      mcpLoaded.value = true
    } catch {
      mcpServers.value = []
    } finally {
      mcpLoading.value = false
    }
  }

  const loadSkills = async (force = false) => {
    if (!force && skillLoaded.value && !skillLoading.value) return
    skillLoading.value = true
    try {
      const res = await toolsAPI.getSkillList()
      const data = res.data?.data || res.data
      skills.value = data.skill || data.skills || []
      skillLoaded.value = true
    } catch {
      skills.value = []
    } finally {
      skillLoading.value = false
    }
  }

  const loadSkillPackages = async (force = false) => {
    if (!force && skillPackageLoaded.value) return
    try {
      const res = await toolsAPI.getSkillPackages()
      const data = res.data?.data || res.data
      skillPackages.value = data?.packages || (Array.isArray(data) ? data : [])
      skillPackageLoaded.value = true
    } catch (e) {
      logger.error('获取技能包列表失败:', e)
    }
  }

  const loadToolMeta = async (force = false) => {
    if (!force && metaLoaded.value && !metaLoading.value) return
    metaLoading.value = true
    try {
      const res = await toolsAPI.getToolMeta()
      toolMeta.value = res.data?.data || { categories: [] }
      metaLoaded.value = true
    } catch {
      toolMeta.value = { categories: [] }
    } finally {
      metaLoading.value = false
    }
  }

  const invalidateLangchainTools = () => { langchainLoaded.value = false }
  const invalidateMcpServers = () => { mcpLoaded.value = false }
  const invalidateSkills = () => { skillLoaded.value = false; skillPackageLoaded.value = false }
  const invalidateAll = () => {
    langchainLoaded.value = false
    mcpLoaded.value = false
    skillLoaded.value = false
    skillPackageLoaded.value = false
    metaLoaded.value = false
  }

  const clearAll = () => {
    langchainTools.value = []
    mcpServers.value = []
    skills.value = []
    skillPackages.value = []
    toolMeta.value = { categories: [] }
    langchainLoaded.value = false
    mcpLoaded.value = false
    skillLoaded.value = false
    skillPackageLoaded.value = false
    metaLoaded.value = false
  }

  return {
    langchainTools,
    mcpServers,
    skills,
    skillPackages,
    toolMeta,
    langchainLoading,
    mcpLoading,
    skillLoading,
    metaLoading,
    systemLangchainTools,
    userLangchainTools,
    systemMcpServers,
    userMcpServers,
    systemSkills,
    userSkills,
    loadLangchainTools,
    loadMcpServers,
    loadSkills,
    loadSkillPackages,
    loadToolMeta,
    invalidateLangchainTools,
    invalidateMcpServers,
    invalidateSkills,
    invalidateAll,
    clearAll,
  }
})
