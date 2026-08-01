<template>
  <el-card class="plan-card">
    <template #header>
      <div class="card-header">
        <span>📚 学习计划</span>
        <el-tag type="info">{{ plan.difficulty }}</el-tag>
      </div>
    </template>

    <h4>{{ plan.topic }}</h4>

    <div class="plan-section">
      <h5>🎯 学习目标</h5>
      <ul>
        <li v-for="(obj, idx) in plan.objectives" :key="idx">{{ obj }}</li>
      </ul>
    </div>

    <div class="plan-section">
      <h5>💡 关键知识点</h5>
      <ul>
        <li v-for="(point, idx) in plan.key_points" :key="idx">{{ point }}</li>
      </ul>
    </div>

    <div class="plan-info">
      <el-tag>预计时间: {{ plan.estimated_time }} 分钟</el-tag>
    </div>
  </el-card>
</template>

<script setup>
/**
 * 学习工作流 - 学习计划展示
 * 渲染学习计划：主题 / 难度 / 学习目标 / 关键知识点 / 预计时间
 */
defineProps({
  /** 学习计划对象（topic / difficulty / objectives / key_points / estimated_time） */
  plan: {
    type: Object,
    required: true,
  },
})
</script>

<style scoped>
.plan-card {
  margin-bottom: 20px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.plan-card h4 {
  margin: 0 0 16px 0;
  font-size: 18px;
}

.plan-section {
  margin-bottom: 16px;
}

.plan-section h5 {
  margin: 0 0 8px 0;
  font-size: 14px;
  color: var(--el-text-color-regular);
}

.plan-section ul {
  margin: 0;
  padding-left: 20px;
}

.plan-section li {
  margin-bottom: 6px;
}

.plan-info {
  margin-top: 16px;
}

@media (max-width: 768px) {
  .card-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }
}
</style>
