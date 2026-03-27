/*
import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [],
})

export default router

*/

// 引入路由核心方法
import { createRouter, createWebHistory } from 'vue-router'

// 路由规则（这里写你的页面路由）
const routes = [
  // 示例路由，后期自己添加
  {
    path: '/',
    name: 'home',
    component: () => import('../views/HomeView.vue')
  },
  {
    path: '/test',
    name: 'test',
    component: () => import('../views/TestPage.vue')
  }



]

// 创建路由实例
const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: routes
})

// 导出路由
export default router
