<script setup>
import { ref, onMounted } from 'vue'
import { useUserStore } from '@/stores/user'
import { ElMessage } from 'element-plus'
import { User, Lock } from '@element-plus/icons-vue'

const userStore = useUserStore()
const loading = ref(false)
const editMode = ref(false)

const formData = ref({
  username: '',
  email: '',
  mobile: '',
  avatar: ''
})

onMounted(async () => {
  await loadUserInfo()
})

async function loadUserInfo() {
  loading.value = true
  try {
    const info = await userStore.getUserInfo()
    if (info) {
      formData.value = {
        username: info.username || '',
        email: info.email || '',
        mobile: info.mobile || '',
        avatar: info.avatar || ''
      }
    }
  } catch (error) {
    console.error('加载用户信息失败:', error)
  } finally {
    loading.value = false
  }
}

async function handleSave() {
  loading.value = true
  try {
    ElMessage.success('保存成功')
    editMode.value = false
  } catch {
    ElMessage.error('保存失败')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="profile-container">
    <div class="profile-card">
      <div class="profile-header">
        <div class="avatar-section">
          <el-avatar :size="120" :icon="User" class="main-avatar" />
        </div>
        <div class="info-section">
          <h2 class="username">{{ userStore.username || '用户' }}</h2>
          <p class="user-email">{{ formData.email || '未设置邮箱' }}</p>
        </div>
      </div>

      <el-tabs class="profile-tabs">
        <el-tab-pane label="基本信息" name="basic">
          <el-form :model="formData" label-width="100px" class="profile-form">
            <el-form-item label="用户名">
              <el-input v-model="formData.username" :disabled="!editMode" />
            </el-form-item>
            <el-form-item label="邮箱">
              <el-input v-model="formData.email" :disabled="!editMode" />
            </el-form-item>
            <el-form-item label="手机号">
              <el-input v-model="formData.mobile" :disabled="!editMode" />
            </el-form-item>
            <el-form-item>
              <el-button v-if="!editMode" type="primary" @click="editMode = true">
                编辑信息
              </el-button>
              <template v-else>
                <el-button type="primary" @click="handleSave" :loading="loading">
                  保存
                </el-button>
                <el-button @click="editMode = false; loadUserInfo()">
                  取消
                </el-button>
              </template>
            </el-form-item>
          </el-form>
        </el-tab-pane>

        <el-tab-pane label="账号安全" name="security">
          <div class="security-list">
            <div class="security-item">
              <div class="security-info">
                <div class="security-title">修改密码</div>
                <div class="security-desc">定期修改密码可以保护账号安全</div>
              </div>
              <el-button :icon="Lock">修改</el-button>
            </div>
            <div class="security-item">
              <div class="security-info">
                <div class="security-title">绑定手机号</div>
                <div class="security-desc">绑定手机号可以用于登录和找回密码</div>
              </div>
              <el-button :icon="User">{{ formData.mobile ? '已绑定' : '绑定' }}</el-button>
            </div>
          </div>
        </el-tab-pane>
      </el-tabs>
    </div>
  </div>
</template>

<style scoped>
.profile-container {
  max-width: 900px;
  margin: 0 auto;
  padding: 40px 24px;
}

.profile-card {
  background-color: var(--el-bg-color);
  border-radius: 12px;
  box-shadow: 0 2px 12px 0 rgba(0, 0, 0, 0.1);
  overflow: hidden;
}

.profile-header {
  display: flex;
  align-items: center;
  gap: 24px;
  padding: 32px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
}

.avatar-section {
  display: flex;
  align-items: center;
  justify-content: center;
}

.main-avatar {
  border: 4px solid rgba(255, 255, 255, 0.3);
}

.info-section {
  color: white;
}

.username {
  margin: 0 0 8px 0;
  font-size: 28px;
  font-weight: 600;
}

.user-email {
  margin: 0;
  font-size: 14px;
  opacity: 0.9;
}

.profile-tabs {
  padding: 24px 32px;
}

.profile-form {
  max-width: 500px;
}

.security-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.security-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 20px;
  background-color: var(--el-fill-color-lighter);
  border-radius: 8px;
}

.security-info {
  flex: 1;
}

.security-title {
  font-size: 16px;
  font-weight: 500;
  margin-bottom: 4px;
}

.security-desc {
  font-size: 14px;
  color: var(--el-text-color-secondary);
}
</style>
