<script setup>
import { useThemeStore } from '../stores/theme'
import { Sunny, Moon } from '@element-plus/icons-vue'

const themeStore = useThemeStore()
</script>

<template>
  <header class="app-header">
    <div class="header-content">
      <div class="header-left">
        <h1 class="app-title">LC-StudyLab</h1>
        <span class="app-subtitle">智能学习 & 研究助手</span>
      </div>
      <div class="header-right">
        <el-dropdown @command="themeStore.setTheme">
          <el-button circle :icon="themeStore.isDark ? Moon : Sunny" />
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="light" :icon="Sunny">浅色</el-dropdown-item>
              <el-dropdown-item command="dark" :icon="Moon">深色</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </div>
  </header>
</template>

<style scoped>
.app-header {
  position: sticky;
  top: 0;
  z-index: 50;
  position: sticky;
  top: 0;
  z-index: 50;
  width: 100%;
  border-bottom: 1px solid var(--el-border-color);
  background-color: var(--el-bg-color);
  backdrop-filter: blur(8px);
  transition: box-shadow 0.2s;
}

.app-header.has-shadow {
  box-shadow: 0 1px 0 var(--el-border-color-lighter);
}

.header-content {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 56px;
  padding: 0 24px;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.app-title {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.app-subtitle {
  font-size: 14px;
  color: var(--el-text-color-secondary);
}

.header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}
</style>
