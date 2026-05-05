<template>
  <div class="not-found-container">
    <div class="not-found-content">
      <div class="not-found-code">404</div>
      <h1 class="not-found-title">页面未找到</h1>
      <p class="not-found-description">抱歉，您访问的页面不存在或已被移除。</p>
      <div class="not-found-actions">
        <router-link to="/" class="home-button">返回首页</router-link>
        <router-link to="/chat" class="chat-button">开始对话</router-link>
      </div>
    </div>
  </div>
</template>

<script setup>
</script>

<style scoped>
.not-found-container {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  padding: 20px;
}

.not-found-content {
  text-align: center;
  background: white;
  border-radius: 20px;
  padding: 60px 40px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
  max-width: 500px;
  width: 100%;
}

.not-found-code {
  font-size: 120px;
  font-weight: 900;
  line-height: 1;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  margin-bottom: 16px;
}

.not-found-title {
  font-size: 28px;
  font-weight: 700;
  color: #1f2937;
  margin: 0 0 12px 0;
}

.not-found-description {
  font-size: 16px;
  color: #6b7280;
  margin: 0 0 32px 0;
}

.not-found-actions {
  display: flex;
  gap: 12px;
  justify-content: center;
  flex-wrap: wrap;
}

@media (max-width: 768px) {
  .not-found-content {
    padding: 40px 24px;
  }

  .not-found-code {
    font-size: 80px;
  }

  .not-found-title {
    font-size: 22px;
  }
}

@media (max-width: 480px) {
  .not-found-container {
    padding: 12px;
  }

  .not-found-content {
    padding: 32px 16px;
    border-radius: 12px;
  }

  .not-found-code {
    font-size: 64px;
  }

  .not-found-title {
    font-size: 18px;
  }

  .not-found-description {
    font-size: 14px;
  }
}
.home-button,
.chat-button {
  padding: 12px 32px;
  border-radius: 10px;
  font-size: 15px;
  font-weight: 600;
  text-decoration: none;
  transition: all 0.2s;
  cursor: pointer;
}

.home-button {
  background: #f3f4f6;
  color: #374151;
}

.home-button:hover {
  background: #e5e7eb;
  transform: translateY(-2px);
}

.chat-button {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
}

.chat-button:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
}
</style>
