import { createRouter, createWebHistory } from 'vue-router'

// P3-5 scaffolding: routes point at placeholder views; P3-6..P3-8 fill them in.
const routes = [
  { path: '/', component: () => import('../views/DashboardView.vue') },
  { path: '/devices', component: () => import('../views/DevicesView.vue') },
  { path: '/alerts', component: () => import('../views/AlertsView.vue') },
  { path: '/work-orders', component: () => import('../views/WorkOrdersView.vue') },
  { path: '/maintenance', component: () => import('../views/MaintenanceView.vue') },
  { path: '/mcp-servers', component: () => import('../views/McpServersView.vue') },
  { path: '/api-endpoints', component: () => import('../views/ExternalApiView.vue') },
  { path: '/chat', component: () => import('../views/ChatView.vue') },
  { path: '/cleanup', component: () => import('../views/CleanupView.vue') },
  { path: '/help', component: () => import('../views/HelpView.vue') },
]

export default createRouter({ history: createWebHistory(), routes })
