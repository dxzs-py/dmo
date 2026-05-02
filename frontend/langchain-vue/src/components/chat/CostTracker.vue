<template>
  <div class="cost-tracker">
    <div class="cost-summary">
      <div class="cost-item main-cost">
        <span class="cost-label">总成本</span>
        <span class="cost-value">{{ costSummary.totalCostFormatted || '$0.0000' }}</span>
      </div>
      <div class="cost-item">
        <span class="cost-label">输入</span>
        <span class="cost-value">{{ formatTokens(costSummary.tokens?.input) }}</span>
      </div>
      <div class="cost-item">
        <span class="cost-label">输出</span>
        <span class="cost-value">{{ formatTokens(costSummary.tokens?.output) }}</span>
      </div>
      <div class="cost-item">
        <span class="cost-label">推理</span>
        <span class="cost-value">{{ formatTokens(costSummary.tokens?.reasoning) }}</span>
      </div>
      <div class="cost-item">
        <span class="cost-label">缓存命中</span>
        <span class="cost-value">{{ formatTokens(costSummary.tokens?.cachedInput) }}</span>
      </div>
    </div>
    <div v-if="costSummary.tokens" class="token-progress">
      <el-progress
        :percentage="tokenPercentage"
        :stroke-width="6"
        :color="progressColor"
        :show-text="false"
      />
      <span class="progress-text">
        {{ formatTokens(costSummary.tokens.total) }} tokens
      </span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  costSummary: {
    type: Object,
    default: () => ({
      totalCostFormatted: '$0.0000',
      tokens: {
        input: 0,
        output: 0,
        reasoning: 0,
        cachedInput: 0,
        total: 0,
      },
    }),
  },
  maxTokens: {
    type: Number,
    default: 128000,
  },
})

const tokenPercentage = computed(() => {
  const total = props.costSummary.tokens?.total || 0
  return Math.min(Math.round((total / props.maxTokens) * 100), 100)
})

const progressColor = computed(() => {
  if (tokenPercentage.value > 80) return '#f56c6c'
  if (tokenPercentage.value > 60) return '#e6a23c'
  return '#409eff'
})

function formatTokens(value) {
  if (!value) return '0'
  if (value >= 1000000) return `${(value / 1000000).toFixed(1)}M`
  if (value >= 1000) return `${(value / 1000).toFixed(1)}K`
  return String(value)
}
</script>

<style scoped>
.cost-tracker {
  padding: 8px 12px;
  border-radius: 8px;
  background: var(--el-bg-color-page);
  font-size: 12px;
}

.cost-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 6px;
}

.cost-item {
  display: flex;
  align-items: center;
  gap: 4px;
}

.cost-item.main-cost {
  width: 100%;
  margin-bottom: 4px;
}

.cost-label {
  color: var(--el-text-color-secondary);
  font-size: 11px;
}

.cost-value {
  font-weight: 600;
  color: var(--el-text-color-primary);
  font-variant-numeric: tabular-nums;
}

.main-cost .cost-value {
  font-size: 16px;
  color: var(--el-color-primary);
}

.token-progress {
  display: flex;
  align-items: center;
  gap: 8px;
}

.token-progress :deep(.el-progress) {
  flex: 1;
}

.progress-text {
  color: var(--el-text-color-secondary);
  font-size: 11px;
  white-space: nowrap;
}
</style>
