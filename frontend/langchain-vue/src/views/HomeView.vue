<script setup>
import { useRouter } from 'vue-router'
import { onMounted } from 'vue'
import { ChatDotRound, Document, Management, Reading, ArrowRight, Setting, FolderOpened, DataAnalysis } from '@element-plus/icons-vue'
import settings from '../config/settings'

const router = useRouter()

const features = [
  {
    title: '智能聊天',
    description: '与AI助手进行自然语言对话，支持多种聊天模式和工具调用',
    icon: ChatDotRound,
    path: '/chat',
    color: '#409eff',
    gradient: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)'
  },
  {
    title: 'RAG 知识库',
    description: '基于文档的智能检索问答系统，快速定位所需信息',
    icon: Document,
    path: '/rag',
    color: '#67c23a',
    gradient: 'linear-gradient(135deg, #11998e 0%, #38ef7d 100%)'
  },
  {
    title: '知识库管理',
    description: '创建和管理知识库，上传文档并构建向量索引',
    icon: FolderOpened,
    path: '/knowledge',
    color: '#9b59b6',
    gradient: 'linear-gradient(135deg, #a18cd1 0%, #fbc2eb 100%)'
  },
  {
    title: '学习工作流',
    description: '自动化的学习流程，帮助您系统地掌握新知识',
    icon: Management,
    path: '/workflows',
    color: '#e6a23c',
    gradient: 'linear-gradient(135deg, #f093fb 0%, #f5576c 100%)'
  },
  {
    title: '深度研究',
    description: '深入的主题研究功能，结合网络搜索和文档分析',
    icon: Reading,
    path: '/deep-research',
    color: '#f56c6c',
    gradient: 'linear-gradient(135deg, #4facfe 0%, #00f2fe 100%)'
  },
  {
    title: '数据分析',
    description: '查看使用统计、Token消耗和成本分析',
    icon: DataAnalysis,
    path: '/dashboard',
    color: '#3498db',
    gradient: 'linear-gradient(135deg, #89f7fe 0%, #66a6ff 100%)'
  }
]

const navigateTo = (path) => {
  router.push(path)
}

onMounted(() => {
  if (settings.HOME_PAGE?.REDIRECT_TO_CHAT) {
    router.push('/chat')
  }
})
</script>

<template>
  <div class="home-view">
    <div class="hero-section">
      <div class="hero-background">
        <div class="floating-shapes">
          <div class="shape shape-1"></div>
          <div class="shape shape-2"></div>
          <div class="shape shape-3"></div>
        </div>
      </div>
      <div class="hero-content">
        <div class="logo-wrapper">
          <div class="logo-icon">
            <el-icon :size="48"><ChatDotRound /></el-icon>
          </div>
        </div>
        <h1 class="hero-title">LC-StudyLab</h1>
        <p class="hero-subtitle">智能学习与研究助手</p>
        <p class="hero-description">
          基于 LangChain 的强大 AI 能力，为您提供智能对话、知识检索、学习规划等全方位服务
        </p>
        <div class="hero-actions">
          <el-button type="primary" size="large" class="primary-btn" @click="navigateTo('/chat')">
            <el-icon><ChatDotRound /></el-icon>
            开始聊天
          </el-button>
          <el-button size="large" class="secondary-btn" @click="navigateTo('/settings')">
            <el-icon><Setting /></el-icon>
            设置
          </el-button>
        </div>
      </div>
      <div class="scroll-indicator">
        <div class="scroll-dot"></div>
      </div>
    </div>

    <div class="features-section">
      <div class="section-header">
        <h2 class="section-title">核心功能</h2>
        <p class="section-subtitle">探索我们为您精心打造的智能工具</p>
      </div>
      <div class="features-grid" v-once>
        <div
          v-for="feature in features"
          :key="feature.path"
          class="feature-card"
          @click="navigateTo(feature.path)"
        >
          <div class="feature-icon-wrapper" :style="{ background: feature.gradient }">
            <div class="feature-icon">
              <el-icon :size="36" color="white">
                <component :is="feature.icon" />
              </el-icon>
            </div>
          </div>
          <h3 class="feature-title">{{ feature.title }}</h3>
          <p class="feature-description">{{ feature.description }}</p>
          <div class="feature-action">
            <span class="action-text">立即体验</span>
            <el-icon class="action-icon"><ArrowRight /></el-icon>
          </div>
        </div>
      </div>
    </div>

    <div class="tech-section" v-once>
      <div class="section-header">
        <h2 class="section-title">技术栈</h2>
        <p class="section-subtitle">构建在最先进的技术之上</p>
      </div>
      <div class="tech-tags">
        <div class="tech-tag">
          <span class="tech-name">Vue 3</span>
        </div>
        <div class="tech-tag">
          <span class="tech-name">Element Plus</span>
        </div>
        <div class="tech-tag">
          <span class="tech-name">Pinia</span>
        </div>
        <div class="tech-tag">
          <span class="tech-name">Django 5.x</span>
        </div>
        <div class="tech-tag">
          <span class="tech-name">LangChain</span>
        </div>
        <div class="tech-tag">
          <span class="tech-name">Vite</span>
        </div>
      </div>
    </div>

    <footer class="footer">
      <p class="footer-text">© 2026 LC-StudyLab. 用心打造，智能相伴。</p>
    </footer>
  </div>
</template>

<style scoped>
.home-view {
  min-height: 100%;
  overflow-y: auto;
  background: #f8fafc;
}

.hero-section {
  position: relative;
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px 20px;
  text-align: center;
  color: white;
  background: linear-gradient(135deg, #1e3a8a 0%, #3730a3 50%, #7c3aed 100%);
  overflow: hidden;
}

.hero-background {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  overflow: hidden;
}

.floating-shapes {
  position: absolute;
  width: 100%;
  height: 100%;
}

.shape {
  position: absolute;
  border-radius: 50%;
  opacity: 0.1;
  animation: float 20s infinite ease-in-out;
}

.shape-1 {
  width: 400px;
  height: 400px;
  background: #fff;
  top: -100px;
  right: -100px;
  animation-delay: 0s;
}

.shape-2 {
  width: 300px;
  height: 300px;
  background: #a78bfa;
  bottom: -50px;
  left: -50px;
  animation-delay: -5s;
}

.shape-3 {
  width: 200px;
  height: 200px;
  background: #60a5fa;
  top: 50%;
  left: 10%;
  animation-delay: -10s;
}

@keyframes float {
  0%, 100% {
    transform: translateY(0) rotate(0deg);
  }
  33% {
    transform: translateY(-30px) rotate(120deg);
  }
  66% {
    transform: translateY(30px) rotate(240deg);
  }
}

.hero-content {
  position: relative;
  z-index: 1;
  max-width: 800px;
  margin: 0 auto;
  animation: fadeInUp 0.8s ease-out;
}

@keyframes fadeInUp {
  from {
    opacity: 0;
    transform: translateY(30px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.logo-wrapper {
  margin-bottom: 32px;
  animation: bounceIn 1s ease-out;
}

@keyframes bounceIn {
  0% {
    transform: scale(0);
  }
  50% {
    transform: scale(1.1);
  }
  100% {
    transform: scale(1);
  }
}

.logo-icon {
  width: 100px;
  height: 100px;
  background: rgba(255, 255, 255, 0.2);
  backdrop-filter: blur(10px);
  border-radius: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0 auto;
  border: 1px solid rgba(255, 255, 255, 0.3);
  box-shadow: 0 20px 40px rgba(0, 0, 0, 0.2);
}

.hero-title {
  font-size: 64px;
  font-weight: 800;
  margin: 0 0 12px;
  background: linear-gradient(135deg, #fff 0%, #e0e7ff 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  letter-spacing: -2px;
}

.hero-subtitle {
  font-size: 28px;
  font-weight: 500;
  margin: 0 0 20px;
  opacity: 0.95;
  letter-spacing: 0.5px;
}

.hero-description {
  font-size: 18px;
  line-height: 1.8;
  margin: 0 0 48px;
  opacity: 0.9;
  max-width: 600px;
  margin-left: auto;
  margin-right: auto;
}

.hero-actions {
  display: flex;
  gap: 20px;
  justify-content: center;
  flex-wrap: wrap;
}

.primary-btn {
  background: linear-gradient(135deg, #3b82f6 0%, #8b5cf6 100%);
  border: none;
  padding: 16px 40px;
  font-size: 18px;
  font-weight: 600;
  border-radius: 12px;
  box-shadow: 0 10px 30px rgba(59, 130, 246, 0.4);
  transition: all 0.3s ease;
}

.primary-btn:hover {
  transform: translateY(-2px);
  box-shadow: 0 15px 40px rgba(59, 130, 246, 0.5);
}

.secondary-btn {
  background: rgba(255, 255, 255, 0.15);
  backdrop-filter: blur(10px);
  border: 1px solid rgba(255, 255, 255, 0.3);
  color: white;
  padding: 16px 40px;
  font-size: 18px;
  font-weight: 600;
  border-radius: 12px;
  transition: all 0.3s ease;
}

.secondary-btn:hover {
  background: rgba(255, 255, 255, 0.25);
  transform: translateY(-2px);
}

@media (max-width: 1024px) {
  .hero-title {
    font-size: 48px;
  }

  .hero-subtitle {
    font-size: 22px;
  }

  .hero-description {
    font-size: 16px;
  }
}

@media (max-width: 768px) {
  .hero-section {
    padding: 32px 16px;
    min-height: auto;
    padding-top: 80px;
    padding-bottom: 80px;
  }

  .hero-title {
    font-size: 36px;
  }

  .hero-subtitle {
    font-size: 18px;
  }

  .hero-description {
    font-size: 15px;
    margin-bottom: 32px;
  }

  .logo-icon {
    width: 80px;
    height: 80px;
  }

  .primary-btn,
  .secondary-btn {
    padding: 14px 32px;
    font-size: 16px;
  }
}

@media (max-width: 480px) {
  .hero-section {
    padding: 24px 12px;
    padding-top: 60px;
    padding-bottom: 60px;
  }

  .hero-title {
    font-size: 28px;
    letter-spacing: -1px;
  }

  .hero-subtitle {
    font-size: 16px;
  }

  .hero-description {
    font-size: 14px;
    margin-bottom: 24px;
  }

  .logo-icon {
    width: 64px;
    height: 64px;
    border-radius: 16px;
  }

  .logo-wrapper {
    margin-bottom: 20px;
  }

  .hero-actions {
    flex-direction: column;
    gap: 12px;
  }

  .primary-btn,
  .secondary-btn {
    width: 100%;
    padding: 12px 24px;
    font-size: 15px;
  }
}
.scroll-indicator {
  position: absolute;
  bottom: 40px;
  left: 50%;
  transform: translateX(-50%);
  animation: bounce 2s infinite;
}

@keyframes bounce {
  0%, 20%, 50%, 80%, 100% {
    transform: translateX(-50%) translateY(0);
  }
  40% {
    transform: translateX(-50%) translateY(-10px);
  }
  60% {
    transform: translateX(-50%) translateY(-5px);
  }
}

.scroll-dot {
  width: 30px;
  height: 50px;
  border: 2px solid rgba(255, 255, 255, 0.5);
  border-radius: 20px;
  position: relative;
}

.scroll-dot::before {
  content: '';
  position: absolute;
  top: 8px;
  left: 50%;
  transform: translateX(-50%);
  width: 6px;
  height: 6px;
  background: white;
  border-radius: 50%;
  animation: scrollDot 1.5s infinite;
}

@keyframes scrollDot {
  0% {
    opacity: 1;
    transform: translateX(-50%) translateY(0);
  }
  100% {
    opacity: 0;
    transform: translateX(-50%) translateY(20px);
  }
}

.features-section {
  padding: 100px 20px;
  background: white;
}

.section-header {
  text-align: center;
  margin-bottom: 64px;
}

.section-title {
  font-size: 42px;
  font-weight: 700;
  margin: 0 0 12px;
  color: #1e293b;
  letter-spacing: -1px;
}

.section-subtitle {
  font-size: 18px;
  color: #64748b;
  margin: 0;
}

.features-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 32px;
  max-width: 1200px;
  margin: 0 auto;
}

.feature-card {
  padding: 40px 32px;
  border-radius: 24px;
  background: #f8fafc;
  cursor: pointer;
  transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1);
  border: 2px solid transparent;
  position: relative;
  overflow: hidden;
}

.feature-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: linear-gradient(135deg, rgba(59, 130, 246, 0.05) 0%, rgba(139, 92, 246, 0.05) 100%);
  opacity: 0;
  transition: opacity 0.4s ease;
}

.feature-card:hover {
  transform: translateY(-8px);
  box-shadow: 0 20px 40px rgba(0, 0, 0, 0.1);
  border-color: #3b82f6;
}

.feature-card:hover::before {
  opacity: 1;
}

.feature-icon-wrapper {
  width: 80px;
  height: 80px;
  border-radius: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0 auto 28px;
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.15);
  position: relative;
  z-index: 1;
}

.feature-icon {
  display: flex;
  align-items: center;
  justify-content: center;
}

.feature-title {
  font-size: 22px;
  font-weight: 700;
  margin: 0 0 12px;
  text-align: center;
  color: #1e293b;
  position: relative;
  z-index: 1;
}

.feature-description {
  font-size: 15px;
  line-height: 1.7;
  margin: 0 0 24px;
  text-align: center;
  color: #64748b;
  position: relative;
  z-index: 1;
}

.feature-action {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  position: relative;
  z-index: 1;
  transition: gap 0.3s ease;
}

.feature-card:hover .feature-action {
  gap: 12px;
}

.action-text {
  font-size: 15px;
  font-weight: 600;
  color: #3b82f6;
}

.action-icon {
  color: #3b82f6;
  transition: transform 0.3s ease;
}

.feature-card:hover .action-icon {
  transform: translateX(4px);
}

.tech-section {
  padding: 100px 20px;
  background: linear-gradient(180deg, #f1f5f9 0%, #e2e8f0 100%);
}

.tech-tags {
  display: flex;
  gap: 20px;
  flex-wrap: wrap;
  justify-content: center;
  max-width: 900px;
  margin: 0 auto;
}

.tech-tag {
  padding: 16px 32px;
  background: white;
  border-radius: 16px;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.08);
  transition: all 0.3s ease;
  cursor: default;
}

.tech-tag:hover {
  transform: translateY(-4px);
  box-shadow: 0 8px 30px rgba(0, 0, 0, 0.12);
}

.tech-name {
  font-size: 16px;
  font-weight: 600;
  color: #374151;
}

.footer {
  padding: 40px 20px;
  background: #1e293b;
  text-align: center;
}

.footer-text {
  font-size: 14px;
  color: #94a3b8;
  margin: 0;
}
</style>
