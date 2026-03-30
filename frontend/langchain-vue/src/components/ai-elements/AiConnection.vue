<template>
  <div class="ai-connection" :class="className">
    <div class="connection-line"></div>
    <div class="connection-arrow"></div>
  </div>
</template>

<script setup>
defineProps({
  className: {
    type: String,
    default: ''
  },
  direction: {
    type: String,
    default: 'horizontal'
  }
})
</script>

<style scoped>
.ai-connection {
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
}

.connection-line {
  height: 2px;
  background-color: var(--el-border-color);
  flex: 1;
}

.connection-arrow {
  width: 0;
  height: 0;
  border-left: 8px solid var(--el-border-color);
  border-top: 5px solid transparent;
  border-bottom: 5px solid transparent;
}
</style>
