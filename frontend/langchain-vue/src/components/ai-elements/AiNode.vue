<template>
  <div class="ai-node" :class="className">
    <div class="node-header">
      <span class="node-title">{{ title }}</span>
      <el-tag v-if="status" :type="getStatusType(status)" size="small">
        {{ status }}
      </el-tag>
    </div>
    <div class="node-content">
      <slot></slot>
    </div>
  </div>
</template>

<script setup>
defineProps({
  className: {
    type: String,
    default: ''
  },
  title: {
    type: String,
    default: 'Node'
  },
  status: {
    type: String,
    default: ''
  }
})

const getStatusType = (status) => {
  const statusMap = {
    'pending': 'info',
    'running': 'warning',
    'completed': 'success',
    'failed': 'danger'
  }
  return statusMap[status] || 'info'
}
</script>

<style scoped>
.ai-node {
  border: 1px solid var(--el-border-color);
  border-radius: 8px;
  background-color: var(--el-bg-color);
  overflow: hidden;
  min-width: 180px;
}

.node-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  background-color: var(--el-fill-color-lighter);
  border-bottom: 1px solid var(--el-border-color);
}

.node-title {
  font-weight: 600;
  font-size: 14px;
  color: var(--el-text-color-primary);
}

.node-content {
  padding: 12px;
}
</style>
