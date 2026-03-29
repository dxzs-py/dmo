<script setup>
defineProps({
  suggestions: {
    type: Array,
    default: () => []
  }
})

const emit = defineEmits(['select'])

const handleSelect = (suggestion) => {
  emit('select', suggestion)
}
</script>

<template>
  <div v-if="suggestions.length > 0" class="ai-suggestion">
    <div class="ai-suggestion__title">建议</div>
    <div class="ai-suggestion__list">
      <button
        v-for="(suggestion, index) in suggestions"
        :key="index"
        class="ai-suggestion__item"
        @click="handleSelect(suggestion)"
      >
        <span class="suggestion-icon">{{ suggestion.icon || '💡' }}</span>
        <span class="suggestion-text">{{ suggestion.text || suggestion }}</span>
      </button>
    </div>
  </div>
</template>

<style scoped>
.ai-suggestion {
  margin: 12px 0;
}

.ai-suggestion__title {
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
  margin-bottom: 8px;
  text-transform: uppercase;
}

.ai-suggestion__list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.ai-suggestion__item {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  background-color: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 20px;
  cursor: pointer;
  font-size: 14px;
  color: var(--el-text-color-primary);
  transition: all 0.2s;
}

.ai-suggestion__item:hover {
  background-color: var(--el-color-primary-light-9);
  border-color: var(--el-color-primary-light-5);
  color: var(--el-color-primary);
}

.suggestion-icon {
  font-size: 16px;
}

.suggestion-text {
  white-space: nowrap;
}
</style>
