import { apiClient } from './axios'

export const capabilityAPI = {
  getConfig(agentType = 'base') {
    return apiClient.get('/context/capability-config/', {
      params: { agent_type: agentType }
    })
  }
}