import { createRouter, createWebHistory } from 'vue-router'
import { useUserStore } from '@/stores/user'

const routes = [
  {
    path: '/',
    name: 'home',
    component: () => import('../views/HomeView.vue'),
    meta: { requiresAuth: false, title: '首页' }
  },
  {
    path: '/login',
    name: 'login',
    component: () => import('../views/LoginView.vue'),
    meta: { requiresAuth: false, guestOnly: true, title: '登录' }
  },
  {
    path: '/register',
    name: 'register',
    component: () => import('../views/RegisterView.vue'),
    meta: { requiresAuth: false, guestOnly: true, title: '注册' }
  },
  {
    path: '/chat',
    name: 'chat',
    component: () => import('../views/ChatView.vue'),
    meta: { requiresAuth: true, title: '智能对话', keepAlive: true }
  },
  {
    path: '/rag',
    name: 'rag',
    component: () => import('../views/RagView.vue'),
    meta: { requiresAuth: true, title: 'RAG 检索', keepAlive: true }
  },
  {
    path: '/workflows',
    name: 'workflows',
    component: () => import('../views/WorkflowView.vue'),
    meta: { requiresAuth: true, title: '学习工作流', keepAlive: true }
  },
  {
    path: '/deep-research',
    name: 'deep-research',
    component: () => import('../views/DeepResearchView.vue'),
    meta: { requiresAuth: true, title: '深度研究', keepAlive: true }
  },
  {
    path: '/profile',
    name: 'profile',
    component: () => import('../views/ProfileView.vue'),
    meta: { requiresAuth: true, title: '个人资料', keepAlive: true }
  },
  {
    path: '/settings',
    name: 'settings',
    component: () => import('../views/SettingsView.vue'),
    meta: { requiresAuth: true, title: '设置', keepAlive: true }
  },
  {
    path: '/knowledge',
    name: 'knowledge',
    component: () => import('../views/KnowledgeBaseView.vue'),
    meta: { requiresAuth: true, title: '知识库管理', keepAlive: true }
  },
  {
    path: '/dashboard',
    name: 'dashboard',
    component: () => import('../views/DashboardView.vue'),
    meta: { requiresAuth: true, title: '数据分析', keepAlive: true }
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: () => import('../views/NotFoundView.vue'),
    meta: { requiresAuth: false, title: '页面未找到' }
  }
]

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: routes
})

const DEFAULT_TITLE = 'LC-StudyLab'

router.beforeEach((to, from, next) => {
  const userStore = useUserStore()

  if (to.meta.requiresAuth && !userStore.isLoggedIn) {
    next({ path: '/login', query: { redirect: to.fullPath } })
  } else if (to.meta.guestOnly && userStore.isLoggedIn) {
    next('/')
  } else {
    next()
  }
})

router.afterEach((to) => {
  const title = to.meta.title
  document.title = title ? `${title} - ${DEFAULT_TITLE}` : DEFAULT_TITLE
})

export default router
