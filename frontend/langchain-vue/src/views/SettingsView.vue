<template>
  <div class="settings-view">
    <el-container>
      <el-header>
        <h2>设置</h2>
      </el-header>
      
      <el-main>
        <el-card>
          <template #header>
            <div class="card-header">
              <span>API 配置</span>
            </div>
          </template>
          
          <el-form :model="settingsForm" label-width="150px">
            <el-form-item label="后端 API 地址">
              <el-input v-model="settingsForm.apiBaseUrl" placeholder="http://localhost:8000/api" />
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
      </el-main>
    </el-container>
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
  height: 100vh;
  display: flex;
  flex-direction: column;
}

.el-container {
  height: 100%;
}

.el-header {
  display: flex;
  align-items: center;
  background: #f5f7fa;
  border-bottom: 1px solid #e4e7ed;
}

.el-header h2 {
  margin: 0;
  font-size: 20px;
  color: #303133;
}

.el-main {
  padding: 20px;
  overflow-y: auto;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
</style>
