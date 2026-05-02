<script setup>
import { ref, onMounted, computed } from 'vue'
import { useUserStore } from '@/stores/user'
import { ElMessage, ElMessageBox } from 'element-plus'
import { User, Lock, Phone, Upload } from '@element-plus/icons-vue'
import { chatAPI, userAPI } from '../api'
import { logger } from '../utils/logger'

const userStore = useUserStore()
const loading = ref(false)
const editMode = ref(false)
const avatarUploading = ref(false)

const passwordDialog = ref(false)
const passwordForm = ref({
  old_password: '',
  new_password: '',
  confirm_password: ''
})
const passwordLoading = ref(false)

const phoneDialog = ref(false)
const phoneForm = ref({ mobile: '' })
const phoneLoading = ref(false)

const formData = ref({
  username: '',
  email: '',
  mobile: '',
  avatar: ''
})

const avatarUrl = computed(() => {
  if (formData.value.avatar) {
    return formData.value.avatar
  }
  return ''
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
    logger.error('加载用户信息失败:', error)
  } finally {
    loading.value = false
  }
}

async function handleSave() {
  loading.value = true
  try {
    const response = await userAPI.updateProfile({
      username: formData.value.username,
      email: formData.value.email,
    })
    if (response.data?.code === 200) {
      ElMessage.success('保存成功')
      editMode.value = false
      await userStore.getUserInfo()
    } else {
      ElMessage.error(response.data?.message || '保存失败')
    }
  } catch (error) {
    ElMessage.error(error.response?.data?.message || '保存失败')
  } finally {
    loading.value = false
  }
}

async function handleAvatarUpload(options) {
  avatarUploading.value = true
  try {
    const formDataObj = new FormData()
    formDataObj.append('avatar', options.file)
    const response = await userAPI.uploadAvatar(formDataObj)
    if (response.data?.code === 200) {
      formData.value.avatar = response.data.data?.avatar || ''
      ElMessage.success('头像更新成功')
      await userStore.getUserInfo()
    }
  } catch (error) {
    ElMessage.error('头像上传失败')
  } finally {
    avatarUploading.value = false
  }
}

function openPasswordDialog() {
  passwordForm.value = { old_password: '', new_password: '', confirm_password: '' }
  passwordDialog.value = true
}

async function handleChangePassword() {
  if (!passwordForm.value.old_password) {
    ElMessage.warning('请输入当前密码')
    return
  }
  if (!passwordForm.value.new_password || passwordForm.value.new_password.length < 8) {
    ElMessage.warning('新密码至少8个字符')
    return
  }
  if (passwordForm.value.new_password !== passwordForm.value.confirm_password) {
    ElMessage.warning('两次输入的密码不一致')
    return
  }

  passwordLoading.value = true
  try {
    const response = await userAPI.changePassword({
      old_password: passwordForm.value.old_password,
      new_password: passwordForm.value.new_password,
    })
    if (response.data?.code === 200) {
      ElMessage.success('密码修改成功，请重新登录')
      passwordDialog.value = false
      await userStore.logout()
    } else {
      ElMessage.error(response.data?.message || '密码修改失败')
    }
  } catch (error) {
    ElMessage.error(error.response?.data?.message || '密码修改失败')
  } finally {
    passwordLoading.value = false
  }
}

function openPhoneDialog() {
  phoneForm.value = { mobile: formData.value.mobile || '' }
  phoneDialog.value = true
}

async function handleBindPhone() {
  if (!phoneForm.value.mobile || !/^1[3-9]\d{9}$/.test(phoneForm.value.mobile)) {
    ElMessage.warning('请输入有效的手机号')
    return
  }

  phoneLoading.value = true
  try {
    const response = await userAPI.bindPhone({ mobile: phoneForm.value.mobile })
    if (response.data?.code === 200) {
      formData.value.mobile = phoneForm.value.mobile
      ElMessage.success('手机号绑定成功')
      phoneDialog.value = false
      await userStore.getUserInfo()
    } else {
      ElMessage.error(response.data?.message || '绑定失败')
    }
  } catch (error) {
    ElMessage.error(error.response?.data?.message || '绑定失败')
  } finally {
    phoneLoading.value = false
  }
}
</script>

<template>
  <div class="profile-container">
    <div class="profile-card">
      <div class="profile-header">
        <div class="avatar-section">
          <el-upload
            :show-file-list="false"
            :http-request="handleAvatarUpload"
            accept="image/png,image/jpeg,image/gif,image/webp"
          >
            <el-avatar :size="120" :src="avatarUrl" :icon="User" class="main-avatar" />
            <div class="avatar-overlay">
              <el-icon :size="24"><Upload /></el-icon>
              <span>更换头像</span>
            </div>
          </el-upload>
        </div>
        <div class="info-section">
          <h2 class="username">{{ userStore.username || '用户' }}</h2>
          <p class="user-email">{{ formData.email || '未设置邮箱' }}</p>
          <p class="user-mobile">{{ formData.mobile || '未绑定手机号' }}</p>
        </div>
      </div>

      <el-tabs class="profile-tabs">
        <el-tab-pane label="基本信息" name="basic">
          <el-form :model="formData" label-width="100px" class="profile-form">
            <el-form-item label="用户名">
              <el-input v-model="formData.username" :disabled="!editMode" />
            </el-form-item>
            <el-form-item label="邮箱">
              <el-input v-model="formData.email" :disabled="!editMode" type="email" />
            </el-form-item>
            <el-form-item label="手机号">
              <el-input v-model="formData.mobile" disabled placeholder="请在账号安全中绑定" />
            </el-form-item>
            <el-form-item>
              <el-button v-if="!editMode" type="primary" @click="editMode = true">
                编辑信息
              </el-button>
              <template v-else>
                <el-button type="primary" :loading="loading" @click="handleSave">
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
                <div class="security-title">
                  <el-icon><Lock /></el-icon>
                  修改密码
                </div>
                <div class="security-desc">定期修改密码可以保护账号安全</div>
              </div>
              <el-button @click="openPasswordDialog">修改</el-button>
            </div>
            <div class="security-item">
              <div class="security-info">
                <div class="security-title">
                  <el-icon><Phone /></el-icon>
                  绑定手机号
                </div>
                <div class="security-desc">绑定手机号可以用于登录和找回密码</div>
              </div>
              <el-button @click="openPhoneDialog">
                {{ formData.mobile ? '更换' : '绑定' }}
              </el-button>
            </div>
          </div>
        </el-tab-pane>
      </el-tabs>
    </div>

    <el-dialog v-model="passwordDialog" title="修改密码" width="420px" :close-on-click-modal="false">
      <el-form :model="passwordForm" label-width="100px">
        <el-form-item label="当前密码">
          <el-input v-model="passwordForm.old_password" type="password" show-password />
        </el-form-item>
        <el-form-item label="新密码">
          <el-input v-model="passwordForm.new_password" type="password" show-password placeholder="至少8个字符" />
        </el-form-item>
        <el-form-item label="确认密码">
          <el-input v-model="passwordForm.confirm_password" type="password" show-password />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="passwordDialog = false">取消</el-button>
        <el-button type="primary" :loading="passwordLoading" @click="handleChangePassword">
          确认修改
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="phoneDialog" title="绑定手机号" width="420px" :close-on-click-modal="false">
      <el-form :model="phoneForm" label-width="100px">
        <el-form-item label="手机号">
          <el-input v-model="phoneForm.mobile" placeholder="请输入11位手机号" maxlength="11" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="phoneDialog = false">取消</el-button>
        <el-button type="primary" :loading="phoneLoading" @click="handleBindPhone">
          确认绑定
        </el-button>
      </template>
    </el-dialog>
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
  position: relative;
  cursor: pointer;
}

.main-avatar {
  border: 4px solid rgba(255, 255, 255, 0.3);
}

.avatar-overlay {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: white;
  opacity: 0;
  transition: opacity 0.3s;
  font-size: 12px;
  gap: 4px;
}

.avatar-section:hover .avatar-overlay {
  opacity: 1;
}

.info-section {
  color: white;
}

.username {
  margin: 0 0 8px 0;
  font-size: 28px;
  font-weight: 600;
}

.user-email, .user-mobile {
  margin: 0 0 4px 0;
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
  display: flex;
  align-items: center;
  gap: 8px;
}

.security-desc {
  font-size: 14px;
  color: var(--el-text-color-secondary);
}

@media (max-width: 768px) {
  .profile-container {
    padding: 16px;
  }

  .profile-header {
    flex-direction: column;
    text-align: center;
    padding: 24px;
  }

  .profile-tabs {
    padding: 16px;
  }
}
</style>
