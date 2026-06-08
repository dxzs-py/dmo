import { apiClient } from './axios'

export const toolsAPI = {
  getMcpTools(params = {}) { return apiClient.get('/tools/mcp/tools/', { params }) },
  testMcpServer(serverName) { return apiClient.post('/tools/mcp/test/', { server_name: serverName }) },
  getMcpServers() { return apiClient.get('/tools/mcp/servers/') },
  addMcpServer(data) { return apiClient.post('/tools/mcp/servers/add/', data) },
  updateMcpServer(data) { return apiClient.post('/tools/mcp/servers/update/', data) },
  deleteMcpServer(name) { return apiClient.post('/tools/mcp/servers/delete/', { name }) },
  toggleMcpServer(data) { return apiClient.post('/tools/mcp/servers/toggle/', data) },
  getToolList(params = {}) { return apiClient.get('/tools/list/', { params }) },
  getToolMeta() { return apiClient.get('/tools/meta/') },
  uploadTool(data) { return apiClient.post('/tools/upload/', data) },
  getCustomTools() { return apiClient.get('/tools/custom/') },
  deleteCustomTool(data) { return apiClient.post('/tools/custom/delete/', data) },
  toggleCustomTool(data) { return apiClient.post('/tools/custom/toggle/', data) },
  updateCustomTool(data) { return apiClient.post('/tools/custom/update/', data) },
  // Skill CRUD
  getSkillList(params = {}) { return apiClient.get('/tools/skills/', { params }) },
  createSkill(data) { return apiClient.post('/tools/skills/create/', data) },
  updateSkill(data) { return apiClient.post('/tools/skills/update/', data) },
  deleteSkill(data) { return apiClient.post('/tools/skills/delete/', data) },
  toggleSkill(data) { return apiClient.post('/tools/skills/toggle/', data) },
  // Skill 包管理（Agent Skills 规范）
  getSkillPackages() { return apiClient.get('/tools/skills/packages/') },
  uploadSkillPackage(formData) { return apiClient.post('/tools/skills/upload/', formData, { headers: { 'Content-Type': 'multipart/form-data' } }) },
  deleteSkillPackage(data) { return apiClient.post('/tools/skills/packages/delete/', data) },
  toggleSkillPackage(data) { return apiClient.post('/tools/skills/packages/toggle/', data) },
  getSkillPackageDetail(params) { return apiClient.get('/tools/skills/packages/detail/', { params }) },
}
