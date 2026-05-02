<script setup>
import { ref, onErrorCaptured } from 'vue'
import { Warning, Refresh, HomeFilled } from '@element-plus/icons-vue'
import { logger } from '../../utils/logger'

const hasError = ref(false)
const errorInfo = ref(null)

onErrorCaptured((error, instance, info) => {
  hasError.value = true
  errorInfo.value = {
    message: error?.message || '未知错误',
    stack: error?.stack || '无堆栈信息',
    component: instance?.$options?.name || instance?.$options?.__name || '未知组件',
    hook: info || '未知生命周期'
  }
  
  logger.error('全局错误捕获:', {
    error,
    component: instance?.$options?.name || instance?.$options?.__name,
    hook: info
  })
  
  return false
})

const reloadPage = () => {
  hasError.value = false
  errorInfo.value = null
  window.location.reload()
}

const goToHome = () => {
  hasError.value = false
  errorInfo.value = null
  window.location.href = '/'
}
</script>

<template>
  <div v-if="hasError" class="error-boundary">
    <div class="error-boundary__content">
      <div class="error-boundary__icon">
        <el-icon :size="64" color="#f56c6c">
          <Warning />
        </el-icon>
      </div>
      <h2 class="error-boundary__title">抱歉，发生了错误</h2>
      <p class="error-boundary__subtitle">系统遇到了一些问题，请尝试以下操作：</p>
      
      <div v-if="errorInfo" class="error-boundary__details">
        <div class="error-boundary__detail-item">
          <span class="error-boundary__detail-label">错误信息：</span>
          <span class="error-boundary__detail-value">{{ errorInfo.message }}</span>
        </div>
        <div v-if="errorInfo.component" class="error-boundary__detail-item">
          <span class="error-boundary__detail-label">出错组件：</span>
          <span class="error-boundary__detail-value">{{ errorInfo.component }}</span>
        </div>
        <div v-if="errorInfo.hook" class="error-boundary__detail-item">
          <span class="error-boundary__detail-label">生命周期：</span>
          <span class="error-boundary__detail-value">{{ errorInfo.hook }}</span>
        </div>
      </div>
      
      <div class="error-boundary__actions">
        <el-button type="primary" @click="reloadPage">
          <el-icon><Refresh /></el-icon>
          刷新页面
        </el-button>
        <el-button @click="goToHome">
          <el-icon><HomeFilled /></el-icon>
          返回首页
        </el-button>
      </div>
    </div>
  </div>
  <slot v-else />
</template>

<style scoped>
.error-boundary {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  background-color: #f8f9fa;
}

.error-boundary__content {
  text-align: center;
  padding: 40px;
  max-width: 600px;
  background: white;
  border-radius: 8px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.1);
}

.error-boundary__icon {
  margin-bottom: 24px;
}

.error-boundary__title {
  font-size: 24px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 12px;
}

.error-boundary__subtitle {
  font-size: 14px;
  color: #606266;
  margin-bottom: 24px;
}

.error-boundary__details {
  text-align: left;
  margin-bottom: 24px;
  padding: 16px;
  background: #f5f7fa;
  border-radius: 4px;
  font-family: 'Courier New', monospace;
  font-size: 12px;
}

.error-boundary__detail-item {
  margin-bottom: 8px;
  line-height: 1.5;
}

.error-boundary__detail-label {
  color: #909399;
  font-weight: 500;
}

.error-boundary__detail-value {
  color: #606266;
  word-break: break-all;
}

.error-boundary__actions {
  display: flex;
  gap: 12px;
  justify-content: center;
}

@media (max-width: 768px) {
  .error-boundary__content {
    padding: 24px;
    margin: 16px;
  }
  
  .error-boundary__title {
    font-size: 20px;
  }
  
  .error-boundary__actions {
    flex-direction: column;
  }
  
  .error-boundary__actions :deep(.el-button) {
    width: 100%;
  }
}
</style>
