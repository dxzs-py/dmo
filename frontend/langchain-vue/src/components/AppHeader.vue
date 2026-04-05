<script setup>
import { useThemeStore } from '../stores/theme'
import { useUserStore } from '../stores/user'
import { useRouter } from 'vue-router'
import { Sunny, Moon, User, Setting, Lock } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'

const themeStore = useThemeStore()
const userStore = useUserStore()
const router = useRouter()

const handleCommand = async (command) => {
  if (command === 'profile') {
    router.push('/profile')
  } else if (command === 'settings') {
    router.push('/settings')
  } else if (command === 'logout') {
    try {
      await ElMessageBox.confirm('确定要退出登录吗？', '提示', {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning'
      })
      userStore.logout()
      ElMessage.success('已退出登录')
      router.push('/')
    } catch {
      // User cancelled
    }
  }
}
</script>

<template>
  <header class="app-header">
    <div class="header-content">
      <div class="header-left">
        <router-link to="/" class="logo-link">
          <h1 class="app-title">LC-StudyLab</h1>
          <span class="app-subtitle">智能学习 & 研究助手</span>
        </router-link>
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

        <div v-if="!userStore.isLoggedIn" class="auth-buttons">
          <router-link to="/login">
            <el-button type="primary">登录</el-button>
          </router-link>
          <router-link to="/register">
            <el-button>注册</el-button>
          </router-link>
        </div>

        <el-dropdown v-else @command="handleCommand" trigger="click">
          <div class="user-info">
            <el-avatar :size="32" :icon="User" />
            <span class="username">{{ userStore.username }}</span>
          </div>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="profile" :icon="User">个人中心</el-dropdown-item>
              <el-dropdown-item command="settings" :icon="Setting">设置</el-dropdown-item>
              <el-dropdown-item divided command="logout" :icon="Lock">退出登录</el-dropdown-item>
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
  width: 100%;
  border-bottom: 1px solid var(--el-border-color);
  background-color: var(--el-bg-color);
  backdrop-filter: blur(8px);
  transition: box-shadow 0.2s;
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

.logo-link {
  display: flex;
  align-items: center;
  gap: 12px;
  text-decoration: none;
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
  gap: 16px;
}

.auth-buttons {
  display: flex;
  gap: 8px;
}

.user-info {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  padding: 4px 8px;
  border-radius: 6px;
  transition: background-color 0.2s;
}

.user-info:hover {
  background-color: var(--el-fill-color-light);
}

.username {
  font-size: 14px;
  color: var(--el-text-color-primary);
}
</style>
