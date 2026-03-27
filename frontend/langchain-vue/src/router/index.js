import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    name: 'home',
    redirect: '/chat'
  },
  {
    path: '/chat',
    name: 'chat',
    component: () => import('../views/ChatView.vue')
  },
  {
    path: '/rag',
    name: 'rag',
    component: () => import('../views/RagView.vue')
  },
  {
    path: '/workflow',
    name: 'workflow',
    component: () => import('../views/WorkflowView.vue')
  },
  {
    path: '/deep-research',
    name: 'deep-research',
    component: () => import('../views/DeepResearchView.vue')
  },
  {
    path: '/settings',
    name: 'settings',
    component: () => import('../views/SettingsView.vue')
  },
  {
    path: '/test',
    name: 'test',
    component: () => import('../views/TestPage.vue')
  }
]

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: routes
})

export default router
