<template>
  <div class="ai-suggestions" :class="className" v-bind="$attrs">
    <el-scrollbar :horizontal="true" height="auto">
      <div class="suggestions-container">
        <slot></slot>
      </div>
    </el-scrollbar>
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
.ai-suggestions {
  width: 100%;
  margin-top: 16px;
}

.suggestions-container {
  display: flex;
  gap: 8px;
  min-width: max-content;
  padding: 4px 0;
}
</style>