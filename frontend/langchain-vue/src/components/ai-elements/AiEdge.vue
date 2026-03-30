<template>
  <div class="ai-edge" :class="className">
    <svg class="edge-svg" viewBox="0 0 100 50">
      <path
        d="M 0 25 Q 50 0 100 25"
        fill="none"
        :stroke="strokeColor"
        stroke-width="2"
      />
      <polygon
        points="95,20 100,25 95,30"
        :fill="strokeColor"
      />
    </svg>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  className: {
    type: String,
    default: ''
  },
  status: {
    type: String,
    default: 'default'
  }
})

const strokeColor = computed(() => {
  const colorMap = {
    'default': 'var(--el-border-color)',
    'active': 'var(--el-color-primary)',
    'success': 'var(--el-color-success)',
    'error': 'var(--el-color-danger)'
  }
  return colorMap[props.status] || colorMap.default
})
</script>

<style scoped>
.ai-edge {
  width: 100px;
  height: 50px;
}

.edge-svg {
  width: 100%;
  height: 100%;
}
</style>
