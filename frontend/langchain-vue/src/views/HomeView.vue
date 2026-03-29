<script setup>
import { useRouter } from 'vue-router'
import { ChatDotRound, Document, Management, Reading, ArrowRight } from '@element-plus/icons-vue'

const router = useRouter()

const features = [
  {
    title: '智能聊天',
    description: '与AI助手进行自然语言对话，支持多种聊天模式和工具调用',
    icon: ChatDotRound,
    path: '/chat',
    color: '#409eff'
  },
  {
    title: 'RAG 知识库',
    description: '基于文档的智能检索问答系统，快速定位所需信息',
    icon: Document,
    path: '/rag',
    color: '#67c23a'
  },
  {
    title: '学习工作流',
    description: '自动化的学习流程，帮助您系统地掌握新知识',
    icon: Management,
    path: '/workflows',
    color: '#e6a23c'
  },
  {
    title: '深度研究',
    description: '深入的主题研究功能，结合网络搜索和文档分析',
    icon: Reading,
    path: '/deep-research',
    color: '#f56c6c'
  }
]

const navigateTo = (path) => {
  router.push(path)
}
</script>

<template>
  <div class="home-view">
    <div class="hero-section">
      <div class="hero-content">
        <h1 class="hero-title">LC-StudyLab</h1>
        <p class="hero-subtitle">智能学习与研究助手</p>
        <p class="hero-description">
          基于 LangChain 的强大 AI 能力，为您提供智能对话、知识检索、学习规划等全方位服务
        </p>
        <div class="hero-actions">
          <el-button type="primary" size="large" @click="navigateTo('/chat')">
            <el-icon><ChatDotRound /></el-icon>
            开始聊天
          </el-button>
          <el-button size="large" @click="navigateTo('/settings')">
            设置
          </el-button>
        </div>
      </div>
    </div>

    <div class="features-section">
      <h2 class="section-title">核心功能</h2>
      <div class="features-grid">
        <div
          v-for="feature in features"
          :key="feature.path"
          class="feature-card"
          @click="navigateTo(feature.path)"
        >
          <div class="feature-icon" :style="{ backgroundColor: feature.color + '20' }">
            <el-icon :size="40" :color="feature.color">
              <component :is="feature.icon" />
            </el-icon>
          </div>
          <h3 class="feature-title">{{ feature.title }}</h3>
          <p class="feature-description">{{ feature.description }}</p>
          <div class="feature-action">
            <el-button type="primary" link>
              立即体验
              <el-icon class="el-icon--right"><ArrowRight /></el-icon>
            </el-button>
          </div>
        </div>
      </div>
    </div>

    <div class="tech-section">
      <h2 class="section-title">技术栈</h2>
      <div class="tech-tags">
        <el-tag size="large" type="info">Vue 3</el-tag>
        <el-tag size="large" type="info">Element Plus</el-tag>
        <el-tag size="large" type="info">Pinia</el-tag>
        <el-tag size="large" type="info">Django 5.x</el-tag>
        <el-tag size="large" type="info">LangChain</el-tag>
        <el-tag size="large" type="info">Vite</el-tag>
      </div>
    </div>
  </div>
</template>

<style scoped>
.home-view {
  min-height: 100%;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  overflow-y: auto;
}

.hero-section {
  padding: 80px 20px;
  text-align: center;
  color: white;
}

.hero-content {
  max-width: 800px;
  margin: 0 auto;
}

.hero-title {
  font-size: 56px;
  font-weight: 700;
  margin: 0 0 16px;
  text-shadow: 2px 2px 4px rgba(0, 0, 0, 0.2);
}

.hero-subtitle {
  font-size: 24px;
  font-weight: 500;
  margin: 0 0 24px;
  opacity: 0.9;
}

.hero-description {
  font-size: 16px;
  line-height: 1.8;
  margin: 0 0 40px;
  opacity: 0.85;
}

.hero-actions {
  display: flex;
  gap: 16px;
  justify-content: center;
}

.features-section {
  padding: 60px 20px;
  background: white;
}

.section-title {
  text-align: center;
  font-size: 32px;
  font-weight: 600;
  margin: 0 0 48px;
  color: #303133;
}

.features-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 24px;
  max-width: 1200px;
  margin: 0 auto;
}

.feature-card {
  padding: 32px 24px;
  border-radius: 16px;
  background: #f5f7fa;
  cursor: pointer;
  transition: all 0.3s ease;
  border: 2px solid transparent;
}

.feature-card:hover {
  transform: translateY(-4px);
  box-shadow: 0 12px 24px rgba(0, 0, 0, 0.1);
  border-color: #409eff;
}

.feature-icon {
  width: 80px;
  height: 80px;
  border-radius: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0 auto 24px;
}

.feature-title {
  font-size: 20px;
  font-weight: 600;
  margin: 0 0 12px;
  text-align: center;
  color: #303133;
}

.feature-description {
  font-size: 14px;
  line-height: 1.6;
  margin: 0 0 20px;
  text-align: center;
  color: #606266;
}

.feature-action {
  text-align: center;
}

.tech-section {
  padding: 60px 20px;
  background: #f5f7fa;
}

.tech-tags {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
  justify-content: center;
  max-width: 800px;
  margin: 0 auto;
}

.tech-tags .el-tag {
  padding: 8px 20px;
  font-size: 14px;
}
</style>
