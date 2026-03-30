<template>
  <div class="ai-canvas" :style="{ background: 'var(--sidebar)' }">
    <slot></slot>
  </div>
</template>

<script setup>
</script>

<style scoped>
.ai-canvas {
  width: 100%;
  height: 100%;
  position: relative;
  overflow: hidden;
}
</style>
