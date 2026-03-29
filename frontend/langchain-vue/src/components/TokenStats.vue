<script setup>
const props = defineProps({
  promptTokens: {
    type: Number,
    default: 0
  },
  completionTokens: {
    type: Number,
    default: 0
  },
  totalTokens: {
    type: Number,
    default: 0
  }
})

const computedTotal = props.totalTokens || (props.promptTokens + props.completionTokens)
</script>

<template>
  <div class="token-stats">
    <div class="token-stat-item">
      <div class="stat-label">提示词</div>
      <div class="stat-value">{{ promptTokens.toLocaleString() }}</div>
    </div>
    <div class="token-divider"></div>
    <div class="token-stat-item">
      <div class="stat-label">生成</div>
      <div class="stat-value">{{ completionTokens.toLocaleString() }}</div>
    </div>
    <div class="token-divider"></div>
    <div class="token-stat-item">
      <div class="stat-label">总计</div>
      <div class="stat-value total">{{ computedTotal.toLocaleString() }}</div>
    </div>
  </div>
</template>

<style scoped>
.token-stats {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 8px 16px;
  background: #f5f7fa;
  border-radius: 8px;
  font-size: 12px;
}

.token-stat-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
}

.stat-label {
  color: #909399;
  font-size: 11px;
}

.stat-value {
  color: #303133;
  font-weight: 600;
  font-size: 14px;
}

.stat-value.total {
  color: #409eff;
}

.token-divider {
  width: 1px;
  height: 24px;
  background: #e4e7ed;
}
</style>