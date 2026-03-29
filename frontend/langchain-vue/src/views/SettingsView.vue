<template>
  <div class="settings-view">
    <div class="view-content">
      <el-card>
        <template #header>
          <div class="card-header">
            <span class="page-title">设置</span>
          </div>
        </template>
        
        <el-form :model="settingsForm" label-width="150px">
          <el-form-item label="后端 API 地址">
            <el-input v-model="settingsForm.apiBaseUrl" placeholder="http://localhost:8000" />
          </el-form-item>
        </el-form>
      </el-card>
      
      <el-card style="margin-top: 20px;">
        <template #header>
          <div class="card-header">
            <span>关于</span>
          </div>
        </template>
        
        <p>LC-StudyLab - 智能学习与研究助手</p>
        <p>版本: 1.0.0</p>
        <p>技术栈: Django 5.x + Vue 3 + LangChain</p>
      </el-card>
    </div>
  </div>
</template>

<script setup>
import { reactive } from 'vue'
import settings from '../settings'

const settingsForm = reactive({
  apiBaseUrl: settings.API_BASE_URL,
})
</script>

<style scoped>
.settings-view {
  height: 100%;
  padding: 24px;
  overflow-y: auto;
}

.view-content {
  max-width: 900px;
  margin: 0 auto;
}

.page-title {
  font-size: 18px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
</style>
