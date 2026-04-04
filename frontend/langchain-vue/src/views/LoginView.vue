<template>
  <div class="login-container">
    <div class="login-box">
      <div class="login-header">
        <h1 class="title">LC-StudyLab</h1>
        <p class="subtitle">智能学习 & 研究助手</p>
      </div>

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

      <form @submit.prevent="handleLogin" class="login-form">
        <div v-if="loginType === 0" class="form-group">
          <input 
            v-model="formData.username"
            type="text"
            placeholder="用户名 / 手机号"
            class="input-field"
            required
          />
        </div>

        <div v-if="loginType === 0" class="form-group">
          <input 
            v-model="formData.password"
            type="password"
            placeholder="密码"
            class="input-field"
            required
          />
        </div>

        <div v-if="loginType === 0" class="form-group captcha-group">
          <input 
            v-model="formData.captcha"
            type="text"
            placeholder="验证码"
            class="input-field captcha-input"
            required
          />
          <img 
            :src="captchaSrc" 
            class="captcha-img" 
            alt="验证码" 
            @click="refreshCaptcha"
            title="点击刷新"
          />
        </div>

        <div v-if="loginType === 1" class="form-group">
          <input 
            v-model="formData.mobile"
            type="text"
            placeholder="手机号"
            class="input-field"
            required
          />
        </div>

        <div v-if="loginType === 1" class="form-group code-group">
          <input 
            v-model="formData.code"
            type="text"
            placeholder="验证码"
            class="input-field code-input"
            required
          />
          <button 
            type="button"
            class="code-button"
            @click="getCode"
            :disabled="countdown > 0"
          >
            {{ countdown > 0 ? `${countdown}秒后重试` : '获取验证码' }}
          </button>
        </div>

        <div v-if="loginType === 0" class="form-options">
          <label class="checkbox-label">
            <input v-model="formData.remember" type="checkbox" />
            <span>记住密码</span>
          </label>
          <a href="#" class="forgot-link">忘记密码？</a>
        </div>

        <button 
          type="submit"
          class="login-button"
          :disabled="loading"
        >
          {{ loading ? '登录中...' : '登录' }}
        </button>

        <p class="register-link">
          还没有账号？
          <router-link to="/register">立即注册</router-link>
        </p>
      </form>

      <div class="back-home">
        <router-link to="/">← 返回首页</router-link>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { ElMessage } from 'element-plus'
import settings from '@/settings'

const router = useRouter()
const userStore = useUserStore()

const loginType = ref(0)
const loading = ref(false)
const countdown = ref(0)
const captchaSrc = ref('')
const captchaKey = ref('')

const formData = reactive({
  username: '',
  password: '',
  captcha: '',
  mobile: '',
  code: '',
  remember: false
})

onMounted(() => {
  refreshCaptcha()
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
  } catch {
    console.error('获取验证码失败')
  }
}

async function handleLogin() {
  if (loginType.value === 0) {
    if (!formData.username || !formData.password) {
      ElMessage.warning('请输入用户名和密码')
      return
    }
    if (!formData.captcha) {
      ElMessage.warning('请输入验证码')
      return
    }
  } else {
    if (!formData.mobile || !formData.code) {
      ElMessage.warning('请输入手机号和验证码')
      return
    }
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
      router.push('/')
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
  if (!formData.mobile) {
    ElMessage.warning('请输入手机号')
    return
  }
  
  ElMessage.success('验证码已发送（演示）')
  countdown.value = 60
  const timer = setInterval(() => {
    countdown.value--
    if (countdown.value <= 0) {
      clearInterval(timer)
    }
  }, 1000)
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

.form-group {
  display: flex;
  flex-direction: column;
}

.form-group.captcha-group {
  flex-direction: row;
  gap: 12px;
}

.form-group.code-group {
  flex-direction: row;
  gap: 12px;
}

.input-field {
  width: 100%;
  padding: 12px 16px;
  border: 2px solid #e5e7eb;
  border-radius: 8px;
  font-size: 14px;
  transition: all 0.2s;
  outline: none;
}

.input-field:focus {
  border-color: #667eea;
  box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
}

.captcha-input {
  flex: 1;
}

.captcha-img {
  height: 46px;
  border-radius: 8px;
  cursor: pointer;
  border: 2px solid #e5e7eb;
  transition: all 0.2s;
}

.captcha-img:hover {
  border-color: #667eea;
}

.code-input {
  flex: 1;
}

.code-button {
  padding: 12px 16px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  border: none;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.2s;
}

.code-button:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
}

.code-button:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.form-options {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.checkbox-label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  color: #6b7280;
  cursor: pointer;
}

.forgot-link {
  font-size: 14px;
  color: #667eea;
  text-decoration: none;
}

.forgot-link:hover {
  text-decoration: underline;
}

.login-button {
  width: 100%;
  padding: 14px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  border: none;
  border-radius: 8px;
  font-size: 16px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s;
  margin-top: 8px;
}

.login-button:hover:not(:disabled) {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
}

.login-button:disabled {
  opacity: 0.6;
  cursor: not-allowed;
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
  padding-top: 24px;
  border-top: 1px solid #e5e7eb;
}

.back-home a {
  color: #6b7280;
  text-decoration: none;
  font-size: 14px;
}

.back-home a:hover {
  color: #667eea;
}
</style>
