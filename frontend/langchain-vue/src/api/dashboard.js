import { apiClient } from './axios'

export const dashboardAPI = {
  getStats() { return apiClient.get('/chat/dashboard/') },
  getRecentActivity(params = {}) { return apiClient.get('/chat/dashboard/activity/', { params }) },
  getUsageChart(params = {}) { return apiClient.get('/chat/dashboard/usage/', { params }) },
}
