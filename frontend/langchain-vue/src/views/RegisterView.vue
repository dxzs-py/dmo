<template>
  <div class="register-container">
    <div class="register-box">
      <div class="register-header">
        <h1 class="title">创建账号</h1>
        <p class="subtitle">加入 LC-StudyLab，开启智能学习之旅</p>
      </div>

      <el-form
        ref="formRef"
        class="register-form"
        :model="formData"
        :rules="rules"
        @submit.prevent="handleRegister"
      >
        <el-form-item prop="username">
          <el-input
            v-model="formData.username"
            placeholder="请输入用户名"
            size="large"
          />
        </el-form-item>

        <el-form-item prop="email">
          <el-input
            v-model="formData.email"
            placeholder="请输入邮箱"
            size="large"
          />
        </el-form-item>

        <el-form-item prop="mobile">
          <el-input
            v-model="formData.mobile"
            placeholder="请输入手机号（可选）"
            size="large"
          />
        </el-form-item>

        <el-form-item prop="password">
          <el-input
            v-model="formData.password"
            type="password"
            placeholder="请输入密码（至少6位）"
            size="large"
            show-password
          />
        </el-form-item>

        <el-form-item prop="passwordConfirm">
          <el-input
            v-model="formData.passwordConfirm"
            type="password"
            placeholder="请再次输入密码"
            size="large"
            show-password
          />
        </el-form-item>

        <el-form-item prop="captcha">
          <div class="captcha-wrapper">
            <el-input
              v-model="formData.captcha"
              placeholder="请输入验证码"
              size="large"
              style="flex: 1"
            />
            <img
              v-if="captchaSrc"
              :src="captchaSrc"
              alt="验证码"
              class="captcha-img"
              title="点击刷新"
              @click="refreshCaptcha"
            />
            <el-button
              v-else
              size="large"
              class="captcha-btn"
              @click="refreshCaptcha"
            >
              获取验证码
            </el-button>
          </div>
        </el-form-item>

        <el-form-item prop="agree">
          <el-checkbox v-model="formData.agree">
            我已阅读并同意
            <a href="#" class="link">《用户协议》</a>
            和
            <a href="#" class="link">《隐私政策》</a>
          </el-checkbox>
        </el-form-item>

        <el-button
          type="primary"
          native-type="submit"
          size="large"
          class="register-button"
          :loading="loading"
        >
          {{ loading ? '注册中...' : '立即注册' }}
        </el-button>

        <p class="login-link">
          已有账号？
          <router-link to="/login">立即登录</router-link>
        </p>
      </el-form>

      <div class="back-home">
        <router-link to="/chat">← 返回</router-link>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { userAPI } from '@/api/user'
import { ElMessage } from 'element-plus'

const router = useRouter()
const userStore = useUserStore()
const formRef = ref(null)

const loading = ref(false)
const captchaSrc = ref('')
const captchaKey = ref('')

const formData = reactive({
  username: '',
  email: '',
  mobile: '',
  password: '',
  passwordConfirm: '',
  captcha: '',
  agree: false
})

const validateUsername = (rule, value, callback) => {
  if (!value) {
    callback(new Error('请输入用户名'))
  } else if (value.length < 3 || value.length > 20) {
    callback(new Error('用户名长度为3-20个字符'))
  } else if (!/^[a-zA-Z0-9]+$/.test(value)) {
    callback(new Error('用户名只能包含字母和数字'))
  } else {
    callback()
  }
}

const validatePassword = (rule, value, callback) => {
  if (!value) {
    callback(new Error('请输入密码'))
  } else if (value.length < 6 || value.length > 128) {
    callback(new Error('密码长度为6-128个字符'))
  } else {
    if (formData.passwordConfirm) {
      formRef.value?.validateField('passwordConfirm')
    }
    callback()
  }
}

const validateConfirmPassword = (rule, value, callback) => {
  if (!value) {
    callback(new Error('请再次输入密码'))
  } else if (value !== formData.password) {
    callback(new Error('两次输入的密码不一致'))
  } else {
    callback()
  }
}

const validateEmail = (rule, value, callback) => {
  if (!value) {
    callback(new Error('请输入邮箱'))
  } else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)) {
    callback(new Error('请输入有效的邮箱地址'))
  } else {
    callback()
  }
}

const validateMobile = (rule, value, callback) => {
  if (!value) {
    callback()
  } else if (!/^1[3-9]\d{9}$/.test(value)) {
    callback(new Error('请输入有效的手机号'))
  } else {
    callback()
  }
}

const validateCaptcha = (rule, value, callback) => {
  if (!value) {
    callback(new Error('请输入验证码'))
  } else {
    callback()
  }
}

const validateAgree = (rule, value, callback) => {
  if (!value) {
    callback(new Error('请同意用户协议和隐私政策'))
  } else {
    callback()
  }
}

const rules = reactive({
  username: [{ validator: validateUsername, trigger: 'blur' }],
  email: [{ validator: validateEmail, trigger: 'blur' }],
  mobile: [{ validator: validateMobile, trigger: 'blur' }],
  password: [{ validator: validatePassword, trigger: 'blur' }],
  passwordConfirm: [{ validator: validateConfirmPassword, trigger: 'blur' }],
  captcha: [{ validator: validateCaptcha, trigger: 'blur' }],
  agree: [{ validator: validateAgree, trigger: 'change' }],
})

const refreshCaptcha = async () => {
  try {
    const response = await userAPI.getCaptcha()
    captchaKey.value = response.headers['x-captcha-key']
    captchaSrc.value = URL.createObjectURL(response.data)
  } catch {
    ElMessage.error('获取验证码失败')
  }
}

onMounted(() => {
  refreshCaptcha()
})

async function handleRegister() {
  if (!formRef.value) return

  try {
    await formRef.value.validate()
  } catch {
    return
  }

  loading.value = true
  try {
    const result = await userStore.register({
      username: formData.username,
      email: formData.email || undefined,
      mobile: formData.mobile || undefined,
      password: formData.password,
      passwordConfirm: formData.passwordConfirm,
      captcha: formData.captcha,
      captchaKey: captchaKey.value
    })

    if (result.success) {
      ElMessage.success(result.message)
      router.push('/login')
    } else {
      ElMessage.error(result.message)
      refreshCaptcha()
    }
  } catch {
    ElMessage.error('注册失败，请稍后重试')
    refreshCaptcha()
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.register-container {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  padding: 20px;
}

.register-box {
  background: var(--el-bg-color-overlay, white);
  border-radius: 16px;
  padding: 40px;
  width: 100%;
  max-width: 420px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
}

.register-header {
  text-align: center;
  margin-bottom: 30px;
}

.title {
  font-size: 28px;
  font-weight: 700;
  color: var(--el-text-color-primary, #1f2937);
  margin: 0 0 8px 0;
}

.subtitle {
  color: var(--el-text-color-secondary, #6b7280);
  font-size: 14px;
  margin: 0;
}

.register-form {
  display: flex;
  flex-direction: column;
  gap: 0;
}

.link {
  color: #667eea;
  text-decoration: none;
}

.link:hover {
  text-decoration: underline;
}

.register-button {
  width: 100%;
  height: 48px;
  font-size: 16px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border: none;
  border-radius: 8px;
  margin-top: 8px;
}

.register-button:hover:not(:disabled) {
  background: linear-gradient(135deg, #5a6fd6 0%, #6a4190 100%);
}

.login-link {
  text-align: center;
  font-size: 14px;
  color: var(--el-text-color-secondary, #6b7280);
  margin: 16px 0 0 0;
}

.login-link a {
  color: #667eea;
  text-decoration: none;
  font-weight: 500;
}

.login-link a:hover {
  text-decoration: underline;
}

.back-home {
  text-align: center;
  margin-top: 24px;
  padding-top: 24px;
  border-top: 1px solid var(--el-border-color, #e5e7eb);
}

.back-home a {
  color: var(--el-text-color-secondary, #6b7280);
  text-decoration: none;
  font-size: 14px;
}

.back-home a:hover {
  color: #667eea;
}

.captcha-wrapper {
  display: flex;
  gap: 12px;
  width: 100%;
}

.captcha-img {
  height: 40px;
  border-radius: 8px;
  cursor: pointer;
  border: 1px solid #dcdfe6;
  transition: border-color 0.2s;
  flex-shrink: 0;
}

.captcha-img:hover {
  border-color: #667eea;
}

@media (max-width: 768px) {
  .register-box {
    padding: 32px 24px;
    max-width: 380px;
  }

  .title {
    font-size: 24px;
  }
}

@media (max-width: 480px) {
  .register-container {
    padding: 12px;
    align-items: flex-start;
    padding-top: 40px;
  }

  .register-box {
    padding: 24px 16px;
    border-radius: 12px;
    max-width: 100%;
  }

  .title {
    font-size: 22px;
  }

  .subtitle {
    font-size: 12px;
  }

  .register-header {
    margin-bottom: 20px;
  }

  .register-button {
    height: 44px;
    font-size: 15px;
  }

  .login-link {
    font-size: 13px;
  }

  .captcha-img {
    height: 36px;
  }
}
</style>
