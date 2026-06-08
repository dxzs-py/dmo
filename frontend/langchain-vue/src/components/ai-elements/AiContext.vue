<template>
  <div class="ai-context" :class="className">
    <div class="context-label">上下文</div>
    <div class="context-content">
      <slot></slot>
    </div>
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
.ai-context {
  padding: 10px 14px;
  border-radius: 10px;
  background-color: color-mix(in srgb, var(--sidebar-primary) 6%, transparent);
}

.context-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--sidebar-primary);
  margin-bottom: 6px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  opacity: 0.85;
}

.context-content {
  font-size: 14px;
  color: var(--foreground);
}
</style>
