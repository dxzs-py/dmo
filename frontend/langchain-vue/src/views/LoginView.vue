<template>
  <div class="login-container">
    <div class="login-box">
      <div class="login-header">
        <h1 class="title">LC-StudyLab</h1>
        <p class="subtitle">智能学习 & 研究助手</p>
      </div>

      <el-alert
        v-if="isExpired"
        type="warning"
        title="登录已过期"
        description="您的登录凭证已过期，请重新登录以继续使用"
        show-icon
        closable
        class="expired-alert"
      />

      <div class="login-tabs">
        <div
          class="tab"
          :class="{ active: loginType === 0 }"
          @click="loginType = 0"
        >
          密码登录
        </div>
        <div
          class="tab"
          :class="{ active: loginType === 1 }"
          @click="loginType = 1"
        >
          验证码登录
        </div>
      </div>

      <el-form
        ref="formRef"
        class="login-form"
        :model="formData"
        :rules="rules"
        @submit.prevent="handleLogin"
      >
        <template v-if="loginType === 0">
          <el-form-item prop="username">
            <el-input
              v-model="formData.username"
              placeholder="用户名 / 手机号"
              prefix-icon="User"
              size="large"
            />
          </el-form-item>

          <el-form-item prop="password">
            <el-input
              v-model="formData.password"
              type="password"
              placeholder="密码"
              prefix-icon="Lock"
              size="large"
              show-password
            />
          </el-form-item>

          <el-form-item prop="captcha">
            <div class="captcha-wrapper">
              <el-input
                v-model="formData.captcha"
                placeholder="验证码"
                prefix-icon="CircleCheck"
                size="large"
                style="flex: 1"
              />
              <img
                :src="captchaSrc"
                class="captcha-img"
                alt="验证码"
                title="点击刷新"
                @click="refreshCaptcha"
              />
            </div>
          </el-form-item>

          <div class="form-options">
            <el-checkbox v-model="formData.remember">记住密码</el-checkbox>
            <a href="#" class="forgot-link">忘记密码？</a>
          </div>
        </template>

        <template v-else>
          <el-form-item prop="mobile">
            <el-input
              v-model="formData.mobile"
              placeholder="手机号"
              prefix-icon="Phone"
              size="large"
            />
          </el-form-item>

          <el-form-item prop="code">
            <div class="code-wrapper">
              <el-input
                v-model="formData.code"
                placeholder="验证码"
                prefix-icon="CircleCheck"
                size="large"
                style="flex: 1"
              />
              <el-button
                size="large"
                :disabled="countdown > 0"
                @click="getCode"
              >
                {{ countdown > 0 ? `${countdown}秒后重试` : '获取验证码' }}
              </el-button>
            </div>
          </el-form-item>
        </template>

        <el-form-item>
          <el-button
            type="primary"
            native-type="submit"
            size="large"
            :loading="loading"
            class="login-button"
          >
            {{ loading ? '登录中...' : '登录' }}
          </el-button>
        </el-form-item>

        <p class="register-link">
          还没有账号？
          <router-link to="/register">立即注册</router-link>
        </p>
      </el-form>

      <div class="back-home">
        <router-link to="/">← 返回首页</router-link>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { ElMessage } from 'element-plus'
import settings from '@/config/settings'
import { logger } from '../utils/logger'

const router = useRouter()
const userStore = useUserStore()
const formRef = ref(null)

const loginType = ref(0)
const loading = ref(false)
const countdown = ref(0)
const captchaSrc = ref('')
const captchaKey = ref('')
const isExpired = ref(false)

const validateUsername = (rule, value, callback) => {
  if (!value) {
    callback(new Error('请输入用户名'))
  } else if (value.length < 2) {
    callback(new Error('用户名至少2个字符'))
  } else {
    callback()
  }
}

const validatePassword = (rule, value, callback) => {
  if (!value) {
    callback(new Error('请输入密码'))
  } else if (value.length < 6) {
    callback(new Error('密码至少6个字符'))
  } else {
    callback()
  }
}

const validateCaptcha = (rule, value, callback) => {
  if (loginType.value === 0 && !value) {
    callback(new Error('请输入验证码'))
  } else {
    callback()
  }
}

const validateMobile = (rule, value, callback) => {
  if (!value) {
    callback(new Error('请输入手机号'))
  } else if (!/^1[3-9]\d{9}$/.test(value)) {
    callback(new Error('请输入有效的手机号'))
  } else {
    callback()
  }
}

const validateCode = (rule, value, callback) => {
  if (!value) {
    callback(new Error('请输入验证码'))
  } else if (!/^\d{4,6}$/.test(value)) {
    callback(new Error('验证码为4-6位数字'))
  } else {
    callback()
  }
}

const formData = reactive({
  username: '',
  password: '',
  captcha: '',
  mobile: '',
  code: '',
  remember: false
})

const baseRules = computed(() => ({
  username: [{ validator: validateUsername, trigger: 'blur' }],
  password: [{ validator: validatePassword, trigger: 'blur' }],
  captcha: [{ validator: validateCaptcha, trigger: 'blur' }],
  mobile: [{ validator: validateMobile, trigger: 'blur' }],
  code: [{ validator: validateCode, trigger: 'blur' }]
}))

const rules = computed(() => {
  if (loginType.value === 0) {
    return {
      username: baseRules.value.username,
      password: baseRules.value.password,
      captcha: baseRules.value.captcha
    }
  }
  return {
    mobile: baseRules.value.mobile,
    code: baseRules.value.code
  }
})

onMounted(() => {
  refreshCaptcha()
  const query = router.currentRoute.value.query
  if (query.expired === '1') {
    isExpired.value = true
  }
})

async function refreshCaptcha() {
  try {
    const response = await fetch(`${settings.API_BASE_URL}/users/captcha/`, {
      method: 'GET',
      credentials: 'include'
    })

    if (response.ok) {
      captchaKey.value = response.headers.get('X-Captcha-Key')
      const blob = await response.blob()
      captchaSrc.value = URL.createObjectURL(blob)
    }
  } catch (error) {
    logger.error('获取验证码失败:', error)
  }
}

async function handleLogin() {
  if (!formRef.value) return

  try {
    await formRef.value.validate()
  } catch {
    return
  }

  loading.value = true
  try {
    let result
    if (loginType.value === 0) {
      result = await userStore.login({
        username: formData.username,
        password: formData.password,
        captcha: formData.captcha,
        captcha_key: captchaKey.value
      })
    } else {
      result = await userStore.login({
        username: formData.mobile,
        password: formData.code
      })
    }

    if (result.success) {
      ElMessage.success(result.message)
      const redirect = router.currentRoute.value.query.redirect || '/'
      router.push(redirect)
    } else {
      ElMessage.error(result.message)
      if (loginType.value === 0) {
        refreshCaptcha()
        formData.captcha = ''
      }
    }
  } catch {
    ElMessage.error('登录失败，请稍后重试')
  } finally {
    loading.value = false
  }
}

function getCode() {
  formRef.value?.validateField('mobile', (error) => {
    if (!error) {
      ElMessage.success('验证码已发送（演示）')
      countdown.value = 60
      const timer = setInterval(() => {
        countdown.value--
        if (countdown.value <= 0) {
          clearInterval(timer)
        }
      }, 1000)
    }
  })
}
</script>

<style scoped>
.login-container {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  padding: 20px;
}

.login-box {
  background: white;
  border-radius: 16px;
  padding: 40px;
  width: 100%;
  max-width: 420px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
}

.login-header {
  text-align: center;
  margin-bottom: 30px;
}

.expired-alert {
  margin-bottom: 20px;
}

.title {
  font-size: 32px;
  font-weight: 700;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  margin: 0 0 8px 0;
}

.subtitle {
  color: #6b7280;
  font-size: 14px;
  margin: 0;
}

.login-tabs {
  display: flex;
  gap: 8px;
  margin-bottom: 24px;
  background: #f3f4f6;
  padding: 4px;
  border-radius: 8px;
}

.tab {
  flex: 1;
  text-align: center;
  padding: 10px 16px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 14px;
  font-weight: 500;
  color: #6b7280;
  transition: all 0.2s;
}

.tab:hover {
  color: #374151;
}

.tab.active {
  background: white;
  color: #667eea;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
}

.login-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
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

.code-wrapper {
  display: flex;
  gap: 12px;
  width: 100%;
}

.form-options {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.forgot-link {
  color: #667eea;
  font-size: 14px;
  text-decoration: none;
  transition: color 0.2s;
}

.forgot-link:hover {
  color: #764ba2;
}

.login-button {
  width: 100%;
  height: 48px;
  font-size: 16px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border: none;
  border-radius: 8px;
}

.login-button:hover {
  background: linear-gradient(135deg, #5a6fd6 0%, #6a4190 100%);
}

.register-link {
  text-align: center;
  font-size: 14px;
  color: #6b7280;
  margin: 16px 0 0 0;
}

.register-link a {
  color: #667eea;
  text-decoration: none;
  font-weight: 500;
}

.register-link a:hover {
  text-decoration: underline;
}

.back-home {
  text-align: center;
  margin-top: 24px;
}

.back-home a {
  color: #6b7280;
  text-decoration: none;
  font-size: 14px;
  transition: color 0.2s;
}

.back-home a:hover {
  color: #667eea;
}
</style>
