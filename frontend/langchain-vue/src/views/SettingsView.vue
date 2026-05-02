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
            <el-input v-model="settingsForm.apiBaseUrl" placeholder="/api/v1" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="isSaving" @click="saveSettings">
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
        </el-form>
      </el-card>

      <el-card style="margin-top: 20px;">
        <template #header>
          <div class="card-header">
            <span>工具权限</span>
          </div>
        </template>
        <PermissionManager />
      </el-card>

      <el-card style="margin-top: 20px;">
        <template #header>
          <div class="card-header">
            <span>模型定价</span>
          </div>
        </template>
        <div v-if="modelPricing" class="pricing-table">
          <el-table :data="pricingData" stripe style="width: 100%">
            <el-table-column prop="model" label="模型" width="200" />
            <el-table-column prop="input" label="输入 ($/M tokens)" width="150" />
            <el-table-column prop="output" label="输出 ($/M tokens)" width="150" />
            <el-table-column prop="cache" label="缓存 ($/M tokens)" />
          </el-table>
        </div>
        <div v-else class="pricing-loading">
          加载定价信息...
        </div>
      </el-card>

      <el-card style="margin-top: 20px;">
        <template #header>
          <div class="card-header">
            <span>关于</span>
          </div>
        </template>

        <p class="about-item">LC-StudyLab - 智能学习与研究助手</p>
        <p class="about-item">版本: 2.0.0</p>
        <p class="about-item">技术栈: Django 5.2 + Vue 3 + LangChain 1.2.13</p>
        <p class="about-item">功能: 智能聊天 / RAG知识库 / 学习工作流 / 深度研究 / 工具权限 / 成本追踪 / 斜杠命令 / 子代理</p>
      </el-card>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref, watch, onMounted, computed } from 'vue'
import { useThemeStore } from '../stores/theme'
import settings from '../config/settings'
import { ElMessage } from 'element-plus'
import { chatAPI } from '../api'
import PermissionManager from '../components/chat/PermissionManager.vue'
import { logger } from '../utils/logger'

const isSaving = ref(false)
const themeStore = useThemeStore()
const modelPricing = ref(null)

const settingsForm = reactive({
  apiBaseUrl: settings.API_BASE_URL,
})

const themeForm = reactive({
  theme: themeStore.currentTheme || 'light',
})

const pricingData = computed(() => {
  if (!modelPricing.value) return []
  return Object.entries(modelPricing.value).map(([model, pricing]) => ({
    model,
    input: `$${pricing.inputPricePerMillion}`,
    output: `$${pricing.outputPricePerMillion}`,
    cache: `$${pricing.cacheReadPricePerMillion}`,
  }))
})

watch(() => themeForm.theme, (newTheme) => {
  themeStore.setTheme(newTheme)
})

const loadPricing = async () => {
  try {
    const res = await chatAPI.getCostInfo()
    if (res.data?.code === 200) {
      modelPricing.value = res.data.data.modelPricing
    }
  } catch (e) {
    logger.error('加载定价信息失败:', e)
  }
}

onMounted(() => {
  loadPricing()
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
    logger.error('保存设置失败:', error)
    ElMessage.error('保存设置失败')
  } finally {
    isSaving.value = false
  }
}

const resetSettings = () => {
  settingsForm.apiBaseUrl = '/api/v1'
  ElMessage.info('已重置为默认值')
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
}

@media (max-width: 480px) {
  .settings-view {
    padding: 8px;
  }
}
</style>
