<template>
  <div class="register-container">
    <div class="register-box">
      <div class="register-header">
        <h1 class="title">创建账号</h1>
        <p class="subtitle">加入 LC-StudyLab，开启智能学习之旅</p>
      </div>

      <form class="register-form" @submit.prevent="handleRegister">
        <div class="form-group">
          <label class="label">用户名</label>
          <input 
            v-model="formData.username"
            type="text"
            placeholder="请输入用户名"
            class="input-field"
            required
          />
        </div>

        <div class="form-group">
          <label class="label">邮箱</label>
          <input 
            v-model="formData.email"
            type="email"
            placeholder="请输入邮箱（可选）"
            class="input-field"
          />
        </div>

        <div class="form-group">
          <label class="label">手机号</label>
          <input 
            v-model="formData.mobile"
            type="text"
            placeholder="请输入手机号（可选）"
            class="input-field"
          />
        </div>

        <div class="form-group">
          <label class="label">密码</label>
          <input 
            v-model="formData.password"
            type="password"
            placeholder="请输入密码（至少6位）"
            class="input-field"
            required
            minlength="6"
          />
        </div>

        <div class="form-group">
          <label class="label">确认密码</label>
          <input 
            v-model="formData.password_confirm"
            type="password"
            placeholder="请再次输入密码"
            class="input-field"
            required
          />
        </div>

        <div class="form-group">
          <label class="label">验证码</label>
          <div class="captcha-wrapper">
            <input 
              v-model="formData.captcha"
              type="text"
              placeholder="请输入验证码"
              class="input-field captcha-input"
              required
              maxlength="6"
            />
            <img
              v-if="captchaSrc"
              :src="captchaSrc"
              alt="验证码"
              class="captcha-img"
              @click="refreshCaptcha"
              title="点击刷新"
            />
            <el-button
              v-else
              size="small"
              @click="refreshCaptcha"
              class="captcha-btn"
            >
              获取验证码
            </el-button>
          </div>
        </div>

        <div class="form-group agree-group">
          <label class="checkbox-label">
            <input v-model="formData.agree" type="checkbox" required />
            <span>我已阅读并同意</span>
            <a href="#" class="link">《用户协议》</a>
            <span>和</span>
            <a href="#" class="link">《隐私政策》</a>
          </label>
        </div>

        <button 
          type="submit"
          class="register-button"
          :disabled="loading"
        >
          {{ loading ? '注册中...' : '立即注册' }}
        </button>

        <p class="login-link">
          已有账号？
          <router-link to="/login">立即登录</router-link>
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
import settings from '@/config/settings'

const router = useRouter()
const userStore = useUserStore()

const loading = ref(false)
const captchaSrc = ref('')
const captchaKey = ref('')

const formData = reactive({
  username: '',
  email: '',
  mobile: '',
  password: '',
  password_confirm: '',
  captcha: '',
  agree: false
})

const refreshCaptcha = async () => {
  try {
    const response = await fetch(`${settings.API_BASE_URL}/users/captcha/`)
    const blob = await response.blob()
    captchaKey.value = response.headers.get('X-Captcha-Key')
    captchaSrc.value = URL.createObjectURL(blob)
  } catch {
    ElMessage.error('获取验证码失败')
  }
}

onMounted(() => {
  refreshCaptcha()
})

async function handleRegister() {
  if (!formData.agree) {
    ElMessage.warning('请先同意用户协议和隐私政策')
    return
  }

  if (formData.password !== formData.password_confirm) {
    ElMessage.warning('两次输入的密码不一致')
    return
  }

  if (formData.password.length < 6) {
    ElMessage.warning('密码长度不能少于6位')
    return
  }

  if (!formData.captcha) {
    ElMessage.warning('请输入验证码')
    return
  }

  loading.value = true
  try {
    const result = await userStore.register({
      username: formData.username,
      email: formData.email || undefined,
      mobile: formData.mobile || undefined,
      password: formData.password,
      password_confirm: formData.password_confirm,
      captcha: formData.captcha,
      captcha_key: captchaKey.value
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
  background: white;
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
  color: #1f2937;
  margin: 0 0 8px 0;
}

.subtitle {
  color: #6b7280;
  font-size: 14px;
  margin: 0;
}

.register-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.form-group.agree-group {
  flex-direction: row;
  align-items: flex-start;
  gap: 8px;
}

.label {
  font-size: 14px;
  font-weight: 500;
  color: #374151;
}

.input-field {
  width: 100%;
  padding: 12px 16px;
  border: 2px solid #e5e7eb;
  border-radius: 8px;
  font-size: 14px;
  transition: all 0.2s;
  outline: none;
  box-sizing: border-box;
}

.input-field:focus {
  border-color: #667eea;
  box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
}

.checkbox-label {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  font-size: 13px;
  color: #6b7280;
  cursor: pointer;
  flex-wrap: wrap;
  line-height: 1.6;
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

.register-button:hover:not(:disabled) {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
}

.register-button:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.login-link {
  text-align: center;
  font-size: 14px;
  color: #6b7280;
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

.captcha-wrapper {
  display: flex;
  gap: 8px;
  align-items: center;
}

.captcha-input {
  flex: 1;
}

.captcha-img {
  height: 42px;
  border-radius: 6px;
  cursor: pointer;
  border: 2px solid #e5e7eb;
  transition: border-color 0.2s;
}

.captcha-img:hover {
  border-color: #667eea;
}

.captcha-btn {
  height: 42px;
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

  .input-field {
    padding: 10px 14px;
    font-size: 13px;
  }

  .register-button {
    padding: 12px;
    font-size: 15px;
  }

  .login-link {
    font-size: 13px;
  }
}
</style>
