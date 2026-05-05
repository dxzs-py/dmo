<script setup>
import { ref, onMounted, onBeforeUnmount, computed } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  TrendCharts, DataLine, Coin, Timer,
  Document, Reading, Management, Monitor,
  Upload, Cpu
} from '@element-plus/icons-vue'
import { dashboardAPI } from '../api'
import { logger } from '../utils/logger'

const route = useRoute()
const loading = ref(false)
const activeTab = ref('overview')

const overview = ref({
  total_sessions: 0,
  total_messages: 0,
  total_tokens: 0,
  total_cost: 0,
  avg_response_time: 0,
  total_events: 0,
  api_requests: 0,
  api_errors: 0,
  total_documents: 0,
  total_workflows: 0,
  total_research: 0,
  cost_breakdown: { chat: 0, research: 0, workflow: 0 },
  token_breakdown: { chat: 0, research: 0, workflow: 0 },
})

const usageTrend = ref([])
const categoryDistribution = ref([])
const featureUsage = ref([])
const modelDistribution = ref([])
const performanceMetrics = ref({
  avg_response_time_ms: 0,
  error_rate: 0,
  total_tracked_requests: 0,
})
const recentActivities = ref([])
const isMockData = ref(false)

const costSummary = computed(() => {
  return overview.value.total_cost.toFixed(4)
})

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

onBeforeUnmount(() => {
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
      usageTrend.value = data.usage_trend || []
      categoryDistribution.value = data.category_distribution || []
      featureUsage.value = data.feature_usage || []
      modelDistribution.value = data.model_distribution || []
      performanceMetrics.value = data.performance_metrics || {}
      recentActivities.value = data.recent_activities || []
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
    total_sessions: 42,
    total_messages: 356,
    total_tokens: 128450,
    total_cost: 2.5678,
    avg_response_time: 3.2,
    total_events: 1240,
    api_requests: 890,
    api_errors: 12,
    total_documents: 28,
    total_workflows: 15,
    total_research: 8,
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
    { name: 'GPT-4o', value: 45 },
    { name: 'GPT-4o-mini', value: 30 },
    { name: 'DeepSeek', value: 15 },
    { name: '其他', value: 10 },
  ]
  performanceMetrics.value = {
    avg_response_time_ms: 1850,
    error_rate: 1.35,
    total_tracked_requests: 890,
  }
  recentActivities.value = [
    { id: 1, event_type_label: '发送聊天消息', event_category_label: '智能聊天', is_success: true, duration_ms: 1200, created_at: new Date().toISOString() },
    { id: 2, event_type_label: 'RAG检索查询', event_category_label: 'RAG检索', is_success: true, duration_ms: 3500, created_at: new Date(Date.now() - 60000).toISOString() },
    { id: 3, event_type_label: '上传文档', event_category_label: '文件操作', is_success: true, duration_ms: 800, created_at: new Date(Date.now() - 120000).toISOString() },
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

function formatDuration(ms) {
  if (!ms) return '-'
  if (ms < 1000) return ms + 'ms'
  return (ms / 1000).toFixed(1) + 's'
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
      <el-tag v-if="isMockData" type="warning" size="small" style="margin-left: 12px">演示数据</el-tag>
    </div>

    <el-tabs v-model="activeTab" class="dashboard-tabs">
      <el-tab-pane label="总览" name="overview">
        <div v-loading="loading">
          <el-row :gutter="16" class="stats-row">
            <el-col :xs="12" :sm="8" :md="4">
              <el-card class="stat-card" shadow="hover">
                <div class="stat-icon" style="background: #409eff;">
                  <el-icon :size="22"><TrendCharts /></el-icon>
                </div>
                <div class="stat-info">
                  <div class="stat-value">{{ overview.total_sessions }}</div>
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
                  <div class="stat-value">{{ formatNumber(overview.total_messages) }}</div>
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
                  <div class="stat-value">{{ overview.total_documents }}</div>
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
                  <div class="stat-value">{{ overview.total_workflows }}</div>
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
                  <div class="stat-value">{{ overview.total_research }}</div>
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
                  <div class="stat-value">${{ costSummary }}</div>
                  <div class="stat-label">总成本</div>
                </div>
              </el-card>
            </el-col>
          </el-row>

          <el-row :gutter="20" class="chart-row">
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

          <el-row :gutter="20" class="chart-row">
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
                       v-memo="[item.name, item.value]">
                    <div class="distribution-header">
                      <span class="distribution-name">{{ item.name }}</span>
                      <span class="distribution-count">{{ item.value }}次</span>
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
          <el-row :gutter="20" class="stats-row">
            <el-col :xs="12" :sm="8">
              <el-card class="stat-card" shadow="hover">
                <div class="stat-icon" style="background: #409eff;">
                  <el-icon :size="22"><Timer /></el-icon>
                </div>
                <div class="stat-info">
                  <div class="stat-value">{{ formatDuration(performanceMetrics.avg_response_time_ms) }}</div>
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
                  <div class="stat-value">{{ performanceMetrics.error_rate }}%</div>
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
                  <div class="stat-value">{{ formatNumber(performanceMetrics.total_tracked_requests) }}</div>
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
                {{ formatDuration(performanceMetrics.avg_response_time_ms) }}
              </el-descriptions-item>
              <el-descriptions-item label="API 错误率">
                {{ performanceMetrics.error_rate }}%
              </el-descriptions-item>
              <el-descriptions-item label="追踪请求数">
                {{ performanceMetrics.total_tracked_requests }}
              </el-descriptions-item>
              <el-descriptions-item label="总 Token 消耗">
                {{ formatNumber(overview.total_tokens) }}
              </el-descriptions-item>
              <el-descriptions-item label="平均每会话消息数">
                {{ overview.total_sessions > 0 ? (overview.total_messages / overview.total_sessions).toFixed(1) : 0 }}
              </el-descriptions-item>
              <el-descriptions-item label="平均每消息 Token 数">
                {{ overview.total_messages > 0 ? Math.round(overview.total_tokens / overview.total_messages) : 0 }}
              </el-descriptions-item>
              <el-descriptions-item label="平均每会话成本">
                ${{ overview.total_sessions > 0 ? (overview.total_cost / overview.total_sessions).toFixed(4) : '0.0000' }}
              </el-descriptions-item>
              <el-descriptions-item label="总事件数">
                {{ formatNumber(overview.total_events) }}
              </el-descriptions-item>
              <el-descriptions-item label="聊天成本">
                ${{ (overview.cost_breakdown?.chat || 0).toFixed(4) }}
              </el-descriptions-item>
              <el-descriptions-item label="深度研究成本">
                ${{ (overview.cost_breakdown?.research || 0).toFixed(4) }}
              </el-descriptions-item>
              <el-descriptions-item label="工作流成本">
                ${{ (overview.cost_breakdown?.workflow || 0).toFixed(4) }}
              </el-descriptions-item>
              <el-descriptions-item label="聊天 Token">
                {{ formatNumber(overview.token_breakdown?.chat || 0) }}
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
              <el-table-column prop="event_type_label" label="事件" min-width="140" />
              <el-table-column prop="event_category_label" label="分类" width="120">
                <template #default="{ row }">
                  <el-tag size="small" :color="getCategoryColor(row.event_category_label)" effect="dark" style="border:none;">
                    {{ row.event_category_label }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="状态" width="80" align="center">
                <template #default="{ row }">
                  <el-tag :type="row.is_success ? 'success' : 'danger'" size="small">
                    {{ row.is_success ? '成功' : '失败' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="耗时" width="100" align="right">
                <template #default="{ row }">
                  {{ formatDuration(row.duration_ms) }}
                </template>
              </el-table-column>
              <el-table-column label="时间" width="140">
                <template #default="{ row }">
                  {{ formatTime(row.created_at) }}
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
