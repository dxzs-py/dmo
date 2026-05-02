<script setup>
import { ref, onMounted, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { TrendCharts, DataLine, Coin, Timer } from '@element-plus/icons-vue'
import { dashboardAPI } from '../api'
import { logger } from '../utils/logger'

const loading = ref(false)
const stats = ref({
  totalSessions: 0,
  totalMessages: 0,
  totalTokens: 0,
  totalCost: 0,
  avgResponseTime: 0,
})

const usageTrend = ref([])
const modelDistribution = ref([])
const modeDistribution = ref([])
const dailyStats = ref([])
const isMockData = ref(false)

const costSummary = computed(() => {
  return stats.value.totalCost.toFixed(4)
})

const trendMax = computed(() => {
  const msgs = Math.max(...usageTrend.value.map(t => t.messages), 1)
  const tokens = Math.max(...usageTrend.value.map(t => t.tokens), 1)
  return { messages: msgs, tokens }
})

onMounted(async () => {
  await loadDashboardData()
})

async function loadDashboardData() {
  loading.value = true
  try {
    const response = await dashboardAPI.getStats()
    if (response.data?.code === 200) {
      const data = response.data.data || {}
      stats.value = {
        totalSessions: data.total_sessions || 0,
        totalMessages: data.total_messages || 0,
        totalTokens: data.total_tokens || 0,
        totalCost: data.total_cost || 0,
        avgResponseTime: data.avg_response_time || 0,
      }
      usageTrend.value = data.usage_trend || []
      modelDistribution.value = data.model_distribution || []
      modeDistribution.value = data.mode_distribution || []
      dailyStats.value = data.daily_stats || []
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
  stats.value = {
    totalSessions: 42,
    totalMessages: 356,
    totalTokens: 128450,
    totalCost: 2.5678,
    avgResponseTime: 3.2,
  }
  usageTrend.value = Array.from({ length: 7 }, (_, i) => ({
    date: new Date(Date.now() - (6 - i) * 86400000).toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' }),
    messages: Math.floor(Math.random() * 50) + 10,
    tokens: Math.floor(Math.random() * 10000) + 5000,
  }))
  modelDistribution.value = [
    { name: 'GPT-4o', value: 45 },
    { name: 'GPT-4o-mini', value: 30 },
    { name: 'DeepSeek', value: 15 },
    { name: '其他', value: 10 },
  ]
  modeDistribution.value = [
    { name: '基础对话', value: 40 },
    { name: '深度思考', value: 20 },
    { name: 'RAG 检索', value: 15 },
    { name: '深度研究', value: 15 },
    { name: '工作流', value: 10 },
  ]
}

function formatNumber(num) {
  if (num >= 1000000) return (num / 1000000).toFixed(1) + 'M'
  if (num >= 1000) return (num / 1000).toFixed(1) + 'K'
  return num.toString()
}
</script>

<template>
  <div class="dashboard-container">
    <div class="dashboard-header">
      <h1>数据分析</h1>
      <p class="subtitle">查看使用统计、Token 消耗和成本分析</p>
      <el-tag v-if="isMockData" type="warning" size="small" style="margin-left: 12px">演示数据</el-tag>
    </div>

    <el-row :gutter="20" v-loading="loading" class="stats-row">
      <el-col :xs="12" :sm="6">
        <el-card class="stat-card" shadow="hover">
          <div class="stat-icon" style="background: #409eff;">
            <el-icon :size="24"><TrendCharts /></el-icon>
          </div>
          <div class="stat-info">
            <div class="stat-value">{{ stats.totalSessions }}</div>
            <div class="stat-label">总会话数</div>
          </div>
        </el-card>
      </el-col>
      <el-col :xs="12" :sm="6">
        <el-card class="stat-card" shadow="hover">
          <div class="stat-icon" style="background: #67c23a;">
            <el-icon :size="24"><DataLine /></el-icon>
          </div>
          <div class="stat-info">
            <div class="stat-value">{{ formatNumber(stats.totalMessages) }}</div>
            <div class="stat-label">总消息数</div>
          </div>
        </el-card>
      </el-col>
      <el-col :xs="12" :sm="6">
        <el-card class="stat-card" shadow="hover">
          <div class="stat-icon" style="background: #e6a23c;">
            <el-icon :size="24"><Coin /></el-icon>
          </div>
          <div class="stat-info">
            <div class="stat-value">{{ formatNumber(stats.totalTokens) }}</div>
            <div class="stat-label">总 Token 数</div>
          </div>
        </el-card>
      </el-col>
      <el-col :xs="12" :sm="6">
        <el-card class="stat-card" shadow="hover">
          <div class="stat-icon" style="background: #f56c6c;">
            <el-icon :size="24"><Timer /></el-icon>
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
                 v-memo="[item.messages, item.tokens, item.date, trendMax.messages, trendMax.tokens]">
              <div class="trend-bar-container">
                <div
                  class="trend-bar messages"
                  :style="{ height: (item.messages / trendMax.messages * 100) + '%' }"
                  :title="`消息: ${item.messages}`"
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
            <span class="legend-item"><span class="legend-dot" style="background:#409eff;"></span>消息数</span>
            <span class="legend-item"><span class="legend-dot" style="background:#67c23a;"></span>Token 数</span>
          </div>
        </el-card>
      </el-col>

      <el-col :xs="24" :md="8">
        <el-card shadow="hover">
          <template #header>
            <span class="card-title">模型使用分布</span>
          </template>
          <div class="distribution-list">
            <div v-for="item in modelDistribution" :key="item.name" class="distribution-item"
                 v-memo="[item.name, item.value]">
              <div class="distribution-name">{{ item.name }}</div>
              <el-progress :percentage="item.value" :stroke-width="12" :show-text="true" />
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="20" class="chart-row">
      <el-col :xs="24" :md="12">
        <el-card shadow="hover">
          <template #header>
            <span class="card-title">对话模式分布</span>
          </template>
          <div class="mode-list">
            <div v-for="item in modeDistribution" :key="item.name" class="mode-item"
                 v-memo="[item.name, item.value]">
              <span class="mode-name">{{ item.name }}</span>
              <div class="mode-bar-container">
                <div class="mode-bar" :style="{ width: item.value + '%' }" />
              </div>
              <span class="mode-value">{{ item.value }}%</span>
            </div>
          </div>
        </el-card>
      </el-col>

      <el-col :xs="24" :md="12">
        <el-card shadow="hover">
          <template #header>
            <span class="card-title">关键指标</span>
          </template>
          <el-descriptions :column="1" border>
            <el-descriptions-item label="平均响应时间">
              {{ stats.avgResponseTime }}s
            </el-descriptions-item>
            <el-descriptions-item label="平均每会话消息数">
              {{ stats.totalSessions > 0 ? (stats.totalMessages / stats.totalSessions).toFixed(1) : 0 }}
            </el-descriptions-item>
            <el-descriptions-item label="平均每消息 Token 数">
              {{ stats.totalMessages > 0 ? Math.round(stats.totalTokens / stats.totalMessages) : 0 }}
            </el-descriptions-item>
            <el-descriptions-item label="平均每会话成本">
              ${{ stats.totalSessions > 0 ? (stats.totalCost / stats.totalSessions).toFixed(4) : '0.0000' }}
            </el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-col>
    </el-row>
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
  gap: 16px;
  width: 100%;
}

.stat-icon {
  width: 48px;
  height: 48px;
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
  font-size: 24px;
  font-weight: 700;
  line-height: 1.2;
}

.stat-label {
  font-size: 13px;
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

.trend-bar.messages {
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
  gap: 16px;
}

.distribution-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.distribution-name {
  font-size: 14px;
  font-weight: 500;
}

.mode-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.mode-item {
  display: flex;
  align-items: center;
  gap: 12px;
}

.mode-name {
  width: 80px;
  font-size: 14px;
  flex-shrink: 0;
}

.mode-bar-container {
  flex: 1;
  height: 12px;
  background: var(--el-fill-color-lighter);
  border-radius: 6px;
  overflow: hidden;
}

.mode-bar {
  height: 100%;
  background: linear-gradient(90deg, #667eea, #764ba2);
  border-radius: 6px;
  transition: width 0.3s;
}

.mode-value {
  width: 40px;
  text-align: right;
  font-size: 14px;
  font-weight: 500;
}

@media (max-width: 768px) {
  .dashboard-container {
    padding: 16px;
  }

  .stat-card :deep(.el-card__body) {
    gap: 8px;
  }

  .stat-value {
    font-size: 18px;
  }

  .stat-icon {
    width: 40px;
    height: 40px;
  }
}
</style>
