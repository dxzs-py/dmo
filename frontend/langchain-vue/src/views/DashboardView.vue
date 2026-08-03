<script setup>
import { ref, onMounted, computed } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  TrendCharts, DataLine, Coin, Timer,
  Document, Reading, Management, Monitor,
  Upload, Cpu
} from '@element-plus/icons-vue'
import { dashboardAPI } from '@/api/dashboard'
import { logger } from '../utils/logger'
import { formatDuration } from '../utils/format'

const route = useRoute()
const loading = ref(false)
const activeTab = ref('overview')

const overview = ref({
  totalSessions: 0,
  totalMessages: 0,
  totalTokens: 0,
  avgResponseTime: 0,
  totalEvents: 0,
  apiRequests: 0,
  apiErrors: 0,
  totalDocuments: 0,
  totalWorkflows: 0,
  totalResearch: 0,
  tokenBreakdown: { chat: 0, research: 0, workflow: 0 },
})

const usageTrend = ref([])
const categoryDistribution = ref([])
const featureUsage = ref([])
const modelDistribution = ref([])
const performanceMetrics = ref({
  avgResponseTimeMs: 0,
  errorRate: 0,
  totalTrackedRequests: 0,
})
const recentActivities = ref([])
const isMockData = ref(false)

const trendMax = computed(() => {
  const events = Math.max(...usageTrend.value.map(t => t.events), 1)
  const tokens = Math.max(...usageTrend.value.map(t => t.tokens), 1)
  return { events, tokens }
})

let pageViewTracked = false

onMounted(async () => {
  await loadDashboardData()
  trackPageView()
})

async function trackPageView() {
  if (pageViewTracked) return
  pageViewTracked = true
  try {
    await dashboardAPI.trackPageView({
      path: route.path,
      title: '数据分析',
    })
  } catch {
  }
}

async function loadDashboardData() {
  loading.value = true
  try {
    const response = await dashboardAPI.getStats()
    if (response.data?.code === 200) {
      const data = response.data.data || {}
      overview.value = data.overview || {}
      usageTrend.value = data.usageTrend || []
      categoryDistribution.value = data.categoryDistribution || []
      featureUsage.value = data.featureUsage || []
      modelDistribution.value = data.modelDistribution || []
      performanceMetrics.value = data.performanceMetrics || {}
      recentActivities.value = data.recentActivities || []
    }
  } catch (error) {
    logger.error('加载仪表盘数据失败:', error)
    loadMockData()
    isMockData.value = true
  } finally {
    loading.value = false
  }
}

function loadMockData() {
  overview.value = {
    totalSessions: 42,
    totalMessages: 356,
    totalTokens: 128450,
    avgResponseTime: 3.2,
    totalEvents: 1240,
    apiRequests: 890,
    apiErrors: 12,
    totalDocuments: 28,
    totalWorkflows: 15,
    totalResearch: 8,
  }
  usageTrend.value = Array.from({ length: 7 }, (_, i) => ({
    date: new Date(Date.now() - (6 - i) * 86400000).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' }),
    events: Math.floor(Math.random() * 80) + 20,
    tokens: Math.floor(Math.random() * 10000) + 5000,
  }))
  categoryDistribution.value = [
    { name: '智能聊天', value: 40, count: 496 },
    { name: 'RAG检索', value: 20, count: 248 },
    { name: '深度研究', value: 15, count: 186 },
    { name: '学习工作流', value: 12, count: 149 },
    { name: '文件操作', value: 8, count: 99 },
    { name: '页面浏览', value: 5, count: 62 },
  ]
  featureUsage.value = [
    { name: '发送聊天消息', value: 280 },
    { name: 'RAG检索查询', value: 120 },
    { name: '上传文档', value: 65 },
    { name: '启动研究', value: 42 },
    { name: '启动工作流', value: 38 },
    { name: '创建会话', value: 35 },
    { name: '页面浏览', value: 28 },
  ]
  modelDistribution.value = [
    { name: 'GPT-4o', value: 45, tokens: 68000 },
    { name: 'GPT-4o-mini', value: 30, tokens: 35000 },
    { name: 'DeepSeek', value: 15, tokens: 18000 },
    { name: '其他', value: 10, tokens: 7450 },
  ]
  performanceMetrics.value = {
    avgResponseTimeMs: 1850,
    errorRate: 1.35,
    totalTrackedRequests: 890,
  }
  recentActivities.value = [
    { id: 1, eventTypeLabel: '发送聊天消息', eventCategoryLabel: '智能聊天', isSuccess: true, durationMs: 1200, createdAt: new Date().toISOString() },
    { id: 2, eventTypeLabel: 'RAG检索查询', eventCategoryLabel: 'RAG检索', isSuccess: true, durationMs: 3500, createdAt: new Date(Date.now() - 60000).toISOString() },
    { id: 3, eventTypeLabel: '上传文档', eventCategoryLabel: '文件操作', isSuccess: true, durationMs: 800, createdAt: new Date(Date.now() - 120000).toISOString() },
  ]
}

function formatNumber(num) {
  if (num >= 1000000) return (num / 1000000).toFixed(1) + 'M'
  if (num >= 1000) return (num / 1000).toFixed(1) + 'K'
  return num.toString()
}

function formatTime(isoStr) {
  if (!isoStr) return ''
  const d = new Date(isoStr)
  return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function formatDurationMs(ms) {
  if (!ms) return '-'
  return formatDuration(ms / 1000)
}

const categoryColors = {
  '智能聊天': '#409eff',
  'RAG检索': '#67c23a',
  '知识库': '#e6a23c',
  '学习工作流': '#f56c6c',
  '深度研究': '#9b59b6',
  '文件操作': '#1abc9c',
  '认证': '#95a5a6',
  '页面浏览': '#3498db',
  'API调用': '#e74c3c',
  '系统': '#7f8c8d',
}

function getCategoryColor(name) {
  return categoryColors[name] || '#409eff'
}
</script>

<template>
  <div class="dashboard-container">
    <div class="dashboard-header">
      <h1>数据分析</h1>
      <p class="subtitle">全面追踪系统使用情况、多维度数据统计与趋势分析</p>
      <el-tag v-if="isMockData" type="warning" size="large" effect="dark" style="margin-left: 12px; font-size: 14px; padding: 4px 14px;">
        ⚠ 演示数据
      </el-tag>
    </div>

    <el-alert
      v-if="isMockData"
      type="warning"
      show-icon
      :closable="false"
      class="mock-data-alert"
    >
      <template #title>
        <span class="mock-alert-title">当前显示的是模拟演示数据，非真实统计信息。请检查后端服务连接是否正常。</span>
      </template>
    </el-alert>

    <el-tabs v-model="activeTab" class="dashboard-tabs">
      <el-tab-pane label="总览" name="overview">
        <div v-loading="loading">
          <el-row :gutter="16" class="stats-row" :class="{ 'mock-data-section': isMockData }">
            <el-col :xs="12" :sm="8" :md="4">
              <el-card class="stat-card" shadow="hover">
                <div class="stat-icon" style="background: #409eff;">
                  <el-icon :size="22"><TrendCharts /></el-icon>
                </div>
                <div class="stat-info">
                  <div class="stat-value">{{ overview.totalSessions }}</div>
                  <div class="stat-label">聊天会话</div>
                </div>
              </el-card>
            </el-col>
            <el-col :xs="12" :sm="8" :md="4">
              <el-card class="stat-card" shadow="hover">
                <div class="stat-icon" style="background: #67c23a;">
                  <el-icon :size="22"><DataLine /></el-icon>
                </div>
                <div class="stat-info">
                  <div class="stat-value">{{ formatNumber(overview.totalMessages) }}</div>
                  <div class="stat-label">聊天消息</div>
                </div>
              </el-card>
            </el-col>
            <el-col :xs="12" :sm="8" :md="4">
              <el-card class="stat-card" shadow="hover">
                <div class="stat-icon" style="background: #e6a23c;">
                  <el-icon :size="22"><Document /></el-icon>
                </div>
                <div class="stat-info">
                  <div class="stat-value">{{ overview.totalDocuments }}</div>
                  <div class="stat-label">知识库文档</div>
                </div>
              </el-card>
            </el-col>
            <el-col :xs="12" :sm="8" :md="4">
              <el-card class="stat-card" shadow="hover">
                <div class="stat-icon" style="background: #9b59b6;">
                  <el-icon :size="22"><Management /></el-icon>
                </div>
                <div class="stat-info">
                  <div class="stat-value">{{ overview.totalWorkflows }}</div>
                  <div class="stat-label">工作流</div>
                </div>
              </el-card>
            </el-col>
            <el-col :xs="12" :sm="8" :md="4">
              <el-card class="stat-card" shadow="hover">
                <div class="stat-icon" style="background: #f56c6c;">
                  <el-icon :size="22"><Reading /></el-icon>
                </div>
                <div class="stat-info">
                  <div class="stat-value">{{ overview.totalResearch }}</div>
                  <div class="stat-label">深度研究</div>
                </div>
              </el-card>
            </el-col>
            <el-col :xs="12" :sm="8" :md="4">
              <el-card class="stat-card" shadow="hover">
                <div class="stat-icon" style="background: #1abc9c;">
                  <el-icon :size="22"><Coin /></el-icon>
                </div>
                <div class="stat-info">
                  <div class="stat-value">{{ formatNumber(overview.totalTokens) }}</div>
                  <div class="stat-label">总 Token</div>
                </div>
              </el-card>
            </el-col>
          </el-row>

          <el-row :gutter="20" class="chart-row" :class="{ 'mock-data-section': isMockData }">
            <el-col :xs="24" :md="16">
              <el-card shadow="hover">
                <template #header>
                  <span class="card-title">使用趋势（近7天）</span>
                </template>
                <div class="trend-chart">
                  <div v-for="item in usageTrend" :key="item.date" class="trend-bar-group"
                       v-memo="[item.events, item.tokens, item.date, trendMax.events, trendMax.tokens]">
                    <div class="trend-bar-container">
                      <div
                        class="trend-bar events"
                        :style="{ height: (item.events / trendMax.events * 100) + '%' }"
                        :title="`事件: ${item.events}`"
                      />
                      <div
                        class="trend-bar tokens"
                        :style="{ height: (item.tokens / trendMax.tokens * 100) + '%' }"
                        :title="`Token: ${item.tokens}`"
                      />
                    </div>
                    <span class="trend-label">{{ item.date }}</span>
                  </div>
                </div>
                <div class="chart-legend">
                  <span class="legend-item"><span class="legend-dot" style="background:#409eff;"></span>事件数</span>
                  <span class="legend-item"><span class="legend-dot" style="background:#67c23a;"></span>Token 数</span>
                </div>
              </el-card>
            </el-col>

            <el-col :xs="24" :md="8">
              <el-card shadow="hover">
                <template #header>
                  <span class="card-title">模块使用分布</span>
                </template>
                <div class="distribution-list">
                  <div v-for="item in categoryDistribution" :key="item.name" class="distribution-item"
                       v-memo="[item.name, item.value, item.count]">
                    <div class="distribution-header">
                      <span class="distribution-name">{{ item.name }}</span>
                      <span class="distribution-count">{{ item.count }}次</span>
                    </div>
                    <el-progress
                      :percentage="item.value"
                      :stroke-width="12"
                      :show-text="true"
                      :color="getCategoryColor(item.name)"
                    />
                  </div>
                </div>
              </el-card>
            </el-col>
          </el-row>

          <el-row :gutter="20" class="chart-row" :class="{ 'mock-data-section': isMockData }">
            <el-col :xs="24" :md="12">
              <el-card shadow="hover">
                <template #header>
                  <span class="card-title">功能使用排行</span>
                </template>
                <div class="feature-list">
                  <div v-for="(item, index) in featureUsage" :key="item.name" class="feature-item"
                       v-memo="[item.name, item.value]">
                    <span class="feature-rank" :class="{ 'top3': index < 3 }">{{ index + 1 }}</span>
                    <span class="feature-name">{{ item.name }}</span>
                    <div class="feature-bar-container">
                      <div class="feature-bar" :style="{ width: (item.value / (featureUsage[0]?.value || 1) * 100) + '%' }" />
                    </div>
                    <span class="feature-value">{{ item.value }}</span>
                  </div>
                </div>
              </el-card>
            </el-col>

            <el-col :xs="24" :md="12">
              <el-card shadow="hover">
                <template #header>
                  <span class="card-title">模型使用分布</span>
                </template>
                <div class="distribution-list">
                  <div v-for="item in modelDistribution" :key="item.name" class="distribution-item"
                       v-memo="[item.name, item.value, item.tokens]">
                    <div class="distribution-header">
                      <span class="distribution-name">{{ item.name }}</span>
                      <span class="distribution-count">{{ item.value }}次 · {{ formatNumber(item.tokens || 0) }} Token</span>
                    </div>
                    <el-progress :percentage="item.value" :stroke-width="12" :show-text="true" />
                  </div>
                </div>
              </el-card>
            </el-col>
          </el-row>
        </div>
      </el-tab-pane>

      <el-tab-pane label="性能指标" name="performance">
        <div v-loading="loading">
          <el-row :gutter="20" class="stats-row" :class="{ 'mock-data-section': isMockData }">
            <el-col :xs="12" :sm="8">
              <el-card class="stat-card" shadow="hover">
                <div class="stat-icon" style="background: #409eff;">
                  <el-icon :size="22"><Timer /></el-icon>
                </div>
                <div class="stat-info">
                  <div class="stat-value">{{ formatDurationMs(performanceMetrics.avgResponseTimeMs) }}</div>
                  <div class="stat-label">平均响应时间</div>
                </div>
              </el-card>
            </el-col>
            <el-col :xs="12" :sm="8">
              <el-card class="stat-card" shadow="hover">
                <div class="stat-icon" style="background: #f56c6c;">
                  <el-icon :size="22"><Monitor /></el-icon>
                </div>
                <div class="stat-info">
                  <div class="stat-value">{{ performanceMetrics.errorRate }}%</div>
                  <div class="stat-label">API 错误率</div>
                </div>
              </el-card>
            </el-col>
            <el-col :xs="12" :sm="8">
              <el-card class="stat-card" shadow="hover">
                <div class="stat-icon" style="background: #67c23a;">
                  <el-icon :size="22"><Cpu /></el-icon>
                </div>
                <div class="stat-info">
                  <div class="stat-value">{{ formatNumber(performanceMetrics.totalTrackedRequests) }}</div>
                  <div class="stat-label">追踪请求数</div>
                </div>
              </el-card>
            </el-col>
          </el-row>

          <el-card shadow="hover" class="chart-row">
            <template #header>
              <span class="card-title">关键指标</span>
            </template>
            <el-descriptions :column="2" border>
              <el-descriptions-item label="平均响应时间">
                {{ formatDurationMs(performanceMetrics.avgResponseTimeMs) }}
              </el-descriptions-item>
              <el-descriptions-item label="API 错误率">
                {{ performanceMetrics.errorRate }}%
              </el-descriptions-item>
              <el-descriptions-item label="追踪请求数">
                {{ performanceMetrics.totalTrackedRequests }}
              </el-descriptions-item>
              <el-descriptions-item label="总 Token 消耗">
                {{ formatNumber(overview.totalTokens) }}
              </el-descriptions-item>
              <el-descriptions-item label="平均每会话消息数">
                {{ overview.totalSessions > 0 ? (overview.totalMessages / overview.totalSessions).toFixed(1) : 0 }}
              </el-descriptions-item>
              <el-descriptions-item label="平均每消息 Token 数">
                {{ overview.totalMessages > 0 ? Math.round(overview.totalTokens / overview.totalMessages) : 0 }}
              </el-descriptions-item>
              <el-descriptions-item label="总事件数">
                {{ formatNumber(overview.totalEvents) }}
              </el-descriptions-item>
              <el-descriptions-item label="聊天 Token">
                {{ formatNumber(overview.tokenBreakdown?.chat || 0) }}
              </el-descriptions-item>
              <el-descriptions-item label="深度研究 Token">
                {{ formatNumber(overview.tokenBreakdown?.research || 0) }}
              </el-descriptions-item>
              <el-descriptions-item label="工作流 Token">
                {{ formatNumber(overview.tokenBreakdown?.workflow || 0) }}
              </el-descriptions-item>
            </el-descriptions>
          </el-card>
        </div>
      </el-tab-pane>

      <el-tab-pane label="最近活动" name="activities">
        <div v-loading="loading">
          <el-card shadow="hover">
            <template #header>
              <span class="card-title">最近操作记录</span>
            </template>
            <el-table :data="recentActivities" stripe style="width: 100%">
              <el-table-column prop="eventTypeLabel" label="事件" min-width="140" />
              <el-table-column prop="eventCategoryLabel" label="分类" width="120">
                <template #default="{ row }">
                  <el-tag size="small" :color="getCategoryColor(row.eventCategoryLabel)" effect="dark" style="border:none;">
                    {{ row.eventCategoryLabel }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="状态" width="80" align="center">
                <template #default="{ row }">
                  <el-tag :type="row.isSuccess ? 'success' : 'danger'" size="small">
                    {{ row.isSuccess ? '成功' : '失败' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="耗时" width="100" align="right">
                <template #default="{ row }">
                  {{ formatDurationMs(row.durationMs) }}
                </template>
              </el-table-column>
              <el-table-column label="时间" width="140">
                <template #default="{ row }">
                  {{ formatTime(row.createdAt) }}
                </template>
              </el-table-column>
            </el-table>
            <el-empty v-if="recentActivities.length === 0" description="暂无活动记录" />
          </el-card>
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style scoped>
.dashboard-container {
  max-width: 1200px;
  margin: 0 auto;
  padding: 32px 24px;
}

.dashboard-header {
  margin-bottom: 24px;
}

.dashboard-header h1 {
  margin: 0 0 8px;
  font-size: 24px;
}

.subtitle {
  color: var(--el-text-color-secondary);
  margin: 0;
}

.mock-data-alert {
  margin-bottom: 16px;
}

.mock-alert-title {
  font-size: 14px;
  font-weight: 500;
}

.mock-data-section {
  position: relative;
  padding: 12px;
  border: 2px dashed #e6a23c;
  border-radius: 8px;
  background: repeating-linear-gradient(
    -45deg,
    transparent,
    transparent 8px,
    rgba(230, 162, 60, 0.04) 8px,
    rgba(230, 162, 60, 0.04) 16px
  );
}

.mock-data-section::before {
  content: '模拟数据';
  position: absolute;
  top: -1px;
  right: 12px;
  background: #e6a23c;
  color: #fff;
  font-size: 11px;
  font-weight: 600;
  padding: 1px 8px;
  border-radius: 0 0 4px 4px;
  z-index: 1;
}

.dashboard-tabs :deep(.el-tabs__header) {
  margin-bottom: 20px;
}

.stats-row {
  margin-bottom: 20px;
}

.stat-card {
  display: flex;
  align-items: center;
  padding: 4px 0;
}

.stat-card :deep(.el-card__body) {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
}

.stat-icon {
  width: 44px;
  height: 44px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  flex-shrink: 0;
}

.stat-info {
  flex: 1;
  min-width: 0;
}

.stat-value {
  font-size: 20px;
  font-weight: 700;
  line-height: 1.2;
}

.stat-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.chart-row {
  margin-bottom: 20px;
}

.card-title {
  font-weight: 600;
}

.trend-chart {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  height: 200px;
  padding: 16px 0;
}

.trend-bar-group {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  height: 100%;
}

.trend-bar-container {
  flex: 1;
  display: flex;
  gap: 4px;
  align-items: flex-end;
  width: 100%;
}

.trend-bar {
  flex: 1;
  border-radius: 4px 4px 0 0;
  transition: height 0.3s;
  min-height: 4px;
}

.trend-bar.events {
  background: #409eff;
}

.trend-bar.tokens {
  background: #67c23a;
}

.trend-label {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  margin-top: 8px;
  white-space: nowrap;
}

.chart-legend {
  display: flex;
  gap: 16px;
  justify-content: center;
  padding-top: 8px;
}

.legend-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.legend-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
}

.distribution-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.distribution-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.distribution-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.distribution-name {
  font-size: 14px;
  font-weight: 500;
}

.distribution-count {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.feature-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.feature-item {
  display: flex;
  align-items: center;
  gap: 10px;
}

.feature-rank {
  width: 24px;
  height: 24px;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 600;
  background: var(--el-fill-color-light);
  color: var(--el-text-color-secondary);
  flex-shrink: 0;
}

.feature-rank.top3 {
  background: #409eff;
  color: white;
}

.feature-name {
  width: 100px;
  font-size: 13px;
  flex-shrink: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.feature-bar-container {
  flex: 1;
  height: 10px;
  background: var(--el-fill-color-lighter);
  border-radius: 5px;
  overflow: hidden;
}

.feature-bar {
  height: 100%;
  background: linear-gradient(90deg, #409eff, #67c23a);
  border-radius: 5px;
  transition: width 0.3s;
}

.feature-value {
  width: 40px;
  text-align: right;
  font-size: 13px;
  font-weight: 500;
  flex-shrink: 0;
}

@media (max-width: 1024px) {
  .dashboard-container {
    padding: 24px 16px;
  }

  .dashboard-header h1 {
    font-size: 20px;
  }

  .trend-chart {
    height: 160px;
  }
}

@media (max-width: 768px) {
  .dashboard-container {
    padding: 16px 12px;
  }

  .dashboard-header h1 {
    font-size: 18px;
  }

  .stat-card :deep(.el-card__body) {
    gap: 8px;
  }

  .stat-value {
    font-size: 16px;
  }

  .stat-icon {
    width: 36px;
    height: 36px;
  }

  .feature-name {
    width: 70px;
  }

  .trend-chart {
    height: 140px;
  }
}

@media (max-width: 480px) {
  .dashboard-container {
    padding: 12px 8px;
  }

  .dashboard-header {
    margin-bottom: 16px;
  }

  .dashboard-header h1 {
    font-size: 16px;
  }

  .subtitle {
    font-size: 12px;
  }

  .stat-value {
    font-size: 14px;
  }

  .stat-icon {
    width: 32px;
    height: 32px;
    border-radius: 8px;
  }

  .stat-label {
    font-size: 11px;
  }

  .trend-chart {
    height: 120px;
    gap: 4px;
  }

  .trend-label {
    font-size: 10px;
  }

  .feature-name {
    width: 60px;
    font-size: 12px;
  }

  .card-title {
    font-size: 14px;
  }
}
</style>
