<template>
  <div class="ai-shimmer" :class="className">
    <div class="shimmer-animation"></div>
  </div>
</template>

<script setup>
defineProps({
  className: {
    type: String,
    default: ''
  }
})
</script>

<style scoped>
.ai-shimmer {
  position: relative;
  overflow: hidden;
  background: var(--el-fill-color);
  border-radius: 4px;
}

.shimmer-animation {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  background: linear-gradient(
    90deg,
    var(--el-fill-color) 0%,
    var(--el-fill-color-light) 50%,
    var(--el-fill-color) 100%
  );
  animation: shimmer 1.5s infinite;
}

@keyframes shimmer {
  0% {
    transform: translateX(-100%);
  }
  100% {
    transform: translateX(100%);
  }
}
</style>
