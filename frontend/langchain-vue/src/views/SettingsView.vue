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
          <el-form-item>
            <el-button type="primary" @click="saveSettings" :loading="isSaving">
              保存设置
            </el-button>
            <el-button @click="resetSettings">
              重置
            </el-button>
          </el-form-item>
        </el-form>
      </el-card>
      
      <el-card style="margin-top: 20px;">
        <template #header>
          <div class="card-header">
            <span>主题设置</span>
          </div>
        </template>
        
        <el-form label-width="150px">
          <el-form-item label="主题模式">
            <el-radio-group v-model="themeForm.theme">
              <el-radio value="light">浅色</el-radio>
              <el-radio value="dark">深色</el-radio>
              <el-radio value="system">跟随系统</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="applyTheme">
              应用主题
            </el-button>
          </el-form-item>
        </el-form>
      </el-card>
      
      <el-card style="margin-top: 20px;">
        <template #header>
          <div class="card-header">
            <span>关于</span>
          </div>
        </template>
        
        <p class="about-item">LC-StudyLab - 智能学习与研究助手</p>
        <p class="about-item">版本: 1.0.0</p>
        <p class="about-item">技术栈: Django 5.x + Vue 3 + LangChain</p>
      </el-card>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useThemeStore } from '../stores/theme'
import settings from '../settings'
import { ElMessage } from 'element-plus'

const isSaving = ref(false)
const themeStore = useThemeStore()

const settingsForm = reactive({
  apiBaseUrl: settings.API_BASE_URL,
})

const themeForm = reactive({
  theme: themeStore.theme || 'system',
})

const saveSettings = async () => {
  if (!settingsForm.apiBaseUrl.trim()) {
    ElMessage.warning('请输入 API 地址')
    return
  }

  isSaving.value = true
  try {
    localStorage.setItem('lc-studylab-api-url', settingsForm.apiBaseUrl)
    settings.API_BASE_URL = settingsForm.apiBaseUrl
    ElMessage.success('设置已保存')
  } catch (error) {
    console.error('保存设置失败:', error)
    ElMessage.error('保存设置失败')
  } finally {
    isSaving.value = false
  }
}

const resetSettings = () => {
  settingsForm.apiBaseUrl = 'http://localhost:8000'
  ElMessage.info('已重置为默认值')
}

const applyTheme = () => {
  themeStore.setTheme(themeForm.theme)
  ElMessage.success('主题已应用')
}
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

.about-item {
  margin: 8px 0;
  line-height: 1.6;
  color: var(--el-text-color-regular);
}

@media (max-width: 768px) {
  .settings-view {
    padding: 12px;
  }

  .view-content {
    max-width: 100%;
  }

  .el-form-item__label {
    width: 100px !important;
  }
}

@media (max-width: 480px) {
  .el-form-item {
    flex-direction: column;
    align-items: flex-start;
  }

  .el-form-item__label {
    width: 100% !important;
    margin-bottom: 8px;
    text-align: left;
  }
}
</style>
