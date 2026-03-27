<script setup>
import { ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { Fold, Expand, ChatDotRound, Document, Management, Reading, Setting } from '@element-plus/icons-vue'

const router = useRouter()
const route = useRoute()

const isCollapse = ref(false)

const menuItems = [
  { index: '/chat', label: '智能聊天', icon: ChatDotRound },
  { index: '/rag', label: 'RAG 知识库', icon: Document },
  { index: '/workflow', label: '学习工作流', icon: Management },
  { index: '/deep-research', label: '深度研究', icon: Reading },
  { index: '/settings', label: '设置', icon: Setting },
]

const handleMenuSelect = (index) => {
  router.push(index)
}
</script>

<template>
  <el-container class="app-container">
    <el-aside :width="isCollapse ? '64px' : '200px'">
      <div class="logo">
        <h2 v-if="!isCollapse">LC-StudyLab</h2>
        <h2 v-else>LC</h2>
      </div>
      <el-menu
        :default-active="route.path"
        :collapse="isCollapse"
        @select="handleMenuSelect"
      >
        <el-menu-item v-for="item in menuItems" :key="item.index" :index="item.index">
          <el-icon><component :is="item.icon" /></el-icon>
          <template #title>{{ item.label }}</template>
        </el-menu-item>
      </el-menu>
    </el-aside>
    
    <el-container>
      <el-header class="app-header">
        <div class="header-left">
          <el-icon class="collapse-btn" @click="isCollapse = !isCollapse">
            <Fold v-if="!isCollapse" />
            <Expand v-else />
          </el-icon>
        </div>
        <div class="header-right">
          <span class="app-title">LC-StudyLab - 智能学习与研究助手</span>
        </div>
      </el-header>
      
      <el-main class="app-main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<style>
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

html, body, #app {
  height: 100%;
  width: 100%;
}

.app-container {
  height: 100%;
}

.el-aside {
  background-color: #304156;
  transition: width 0.3s;
  overflow: hidden;
}

.logo {
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: center;
  background-color: #2b3a4a;
  color: white;
}

.logo h2 {
  font-size: 18px;
  margin: 0;
  white-space: nowrap;
}

.el-menu {
  border-right: none;
  background-color: #304156;
}

.el-menu-item {
  color: #bfcbd9;
}

.el-menu-item:hover {
  background-color: #263445 !important;
}

.el-menu-item.is-active {
  background-color: #409eff !important;
  color: white;
}

.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background-color: #fff;
  border-bottom: 1px solid #e4e7ed;
  padding: 0 20px;
}

.header-left {
  display: flex;
  align-items: center;
}

.collapse-btn {
  font-size: 20px;
  cursor: pointer;
  color: #606266;
}

.collapse-btn:hover {
  color: #409eff;
}

.header-right {
  display: flex;
  align-items: center;
}

.app-title {
  font-size: 16px;
  color: #303133;
  font-weight: 500;
}

.app-main {
  background-color: #f0f2f5;
  padding: 0;
  overflow: hidden;
}
</style>
