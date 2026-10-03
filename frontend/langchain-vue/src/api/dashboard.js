import { apiClient } from './axios'

export const dashboardAPI = {
  getStats() { return apiClient.get('/analytics/dashboard/') },
  trackPageView(data) { return apiClient.post('/analytics/track/page-view/', data) },
}
