import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import { capabilityAPI } from '@/api/capability'
import { useUserStore } from './user'
import { logger } from '@/utils/logger'

export const useCapabilityStore = defineStore('capability', () => {
  const availableCapabilities = ref([])
  const defaultCapabilities = ref([])
  const loaded = ref(false)

  async function fetchCapabilityConfig(agentType = 'base') {
    const userStore = useUserStore()
    if (!userStore.isLoggedIn) return
    try {
      const res = await capabilityAPI.getConfig(agentType)
      if (res.data && res.data.code === 200) {
        availableCapabilities.value = res.data.data.availableCapabilities || []
        defaultCapabilities.value = res.data.data.defaultCapabilities || []
        loaded.value = true
      }
    } catch (e) {
      logger.warn('获取能力配置失败:', e)
    }
  }

  // 登录后自动获取，登出后清空
  const userStore = useUserStore()
  watch(() => userStore.isLoggedIn, (newVal) => {
    if (newVal) {
      fetchCapabilityConfig()
    } else {
      availableCapabilities.value = []
      defaultCapabilities.value = []
      loaded.value = false
    }
  })

  return {
    availableCapabilities,
    defaultCapabilities,
    loaded,
    fetchCapabilityConfig,
  }
})
