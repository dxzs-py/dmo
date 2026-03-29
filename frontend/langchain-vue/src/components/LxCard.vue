<script setup>
defineProps({
  shadow: {
    type: String,
    default: 'always',
    validator: (v) => ['always', 'hover', 'never'].includes(v)
  },
  bodyStyle: {
    type: [String, Object],
    default: () => ({ padding: '20px' })
  }
})
</script>

<template>
  <div :class="['lx-card', `is-${shadow}-shadow`]">
    <div v-if="$slots.header || $slots.header" class="lx-card__header">
      <slot name="header">
        <span>{{ header }}</span>
      </slot>
    </div>
    <div class="lx-card__body" :style="bodyStyle">
      <slot />
    </div>
  </div>
</template>

<style scoped>
.lx-card {
  border-radius: 4px;
  border: 1px solid var(--el-border-color-light);
  background-color: var(--el-bg-color);
  overflow: hidden;
  transition: all 0.3s;
}

.lx-card.is-always-shadow {
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.1);
}

.lx-card.is-hover-shadow:hover {
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.15);
}

.lx-card.is-never-shadow {
  box-shadow: none;
}

.lx-card__header {
  padding: 18px 20px;
  border-bottom: 1px solid var(--el-border-color-light);
  font-weight: 600;
  font-size: 16px;
  color: var(--el-text-color-primary);
}

.lx-card__body {
  padding: 20px;
}
</style>
