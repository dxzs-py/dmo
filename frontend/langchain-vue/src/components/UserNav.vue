<template>
  <div class="user-nav">
    <template v-if="userStore.isLoggedIn">
      <div class="user-info">
        <el-dropdown @command="handleCommand">
          <span class="user-dropdown">
            <span class="username">{{ userStore.username || '用户' }}</span>
            <el-icon class="el-icon--right"><arrow-down /></el-icon>
          </span>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="settings">
                <el-icon><setting /></el-icon>
                设置
              </el-dropdown-item>
              <el-dropdown-item divided command="logout">
                <el-icon><switch-button /></el-icon>
                退出登录
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </template>
    <template v-else>
      <div class="auth-buttons">
        <router-link to="/login" class="nav-link">登录</router-link>
        <span class="divider">|</span>
        <router-link to="/register" class="nav-link register">注册</router-link>
      </div>
    </template>
  </div>
</template>

<script setup>
import { useUserStore } from '@/stores/user'
import { useRouter } from 'vue-router'
import { ElMessageBox, ElMessage } from 'element-plus'
import { ArrowDown, Setting, SwitchButton } from '@element-plus/icons-vue'

const userStore = useUserStore()
const router = useRouter()

function handleCommand(command) {
  if (command === 'settings') {
    router.push('/settings')
  } else if (command === 'logout') {
    ElMessageBox.confirm('确定要退出登录吗？', '提示', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning',
      appendToBody: true
    }).then(() => {
      userStore.logout()
      ElMessage.success('已退出登录')
      router.push('/')
    }).catch(() => {})
  }
}
</script>

<style scoped>
.user-nav {
  display: flex;
  align-items: center;
  gap: 12px;
}

.user-info {
  display: flex;
  align-items: center;
}

.user-dropdown {
  display: flex;
  align-items: center;
  gap: 4px;
  cursor: pointer;
  padding: 6px 12px;
  border-radius: 6px;
  transition: background-color 0.2s;
}

.user-dropdown:hover {
  background-color: rgba(102, 126, 234, 0.1);
}

.username {
  font-weight: 500;
  color: #374151;
}

.auth-buttons {
  display: flex;
  align-items: center;
  gap: 8px;
}

.nav-link {
  color: #6b7280;
  text-decoration: none;
  font-size: 14px;
  padding: 6px 12px;
  border-radius: 6px;
  transition: all 0.2s;
}

.nav-link:hover {
  color: #667eea;
  background-color: rgba(102, 126, 234, 0.1);
}

.nav-link.register {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  padding: 8px 16px;
}

.nav-link.register:hover {
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
}

.divider {
  color: #d1d5db;
}
</style>
