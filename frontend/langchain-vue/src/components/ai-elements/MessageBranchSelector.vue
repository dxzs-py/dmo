<template>
  <div v-if="totalBranchesValue > 1" class="message-branch-selector" :class="className" v-bind="$attrs">
    <button class="branch-btn branch-prev" :disabled="totalBranchesValue <= 1" @click="goToPrevious">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>
    </button>
    <span class="branch-page">
      <span class="branch-current">{{ currentBranchValue + 1 }}</span>
      <span class="branch-sep">/</span>
      <span class="branch-total">{{ totalBranchesValue }}</span>
    </span>
    <button class="branch-btn branch-next" :disabled="totalBranchesValue <= 1" @click="goToNext">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>
    </button>
  </div>
</template>

<script setup>
import { inject, computed } from 'vue'

defineProps({
  from: {
    type: String,
    default: 'assistant'
  },
  className: {
    type: String,
    default: ''
  }
})

const branchContext = inject('messageBranchContext')

if (!branchContext) {
  throw new Error('MessageBranchSelector must be used within MessageBranch')
}

const currentBranchValue = computed(() => {
  const val = branchContext.currentBranch
  return typeof val === 'object' && val !== null && 'value' in val ? val.value : (val ?? 0)
})

const totalBranchesValue = computed(() => {
  const val = branchContext.totalBranches
  return typeof val === 'object' && val !== null && 'value' in val ? val.value : (val ?? 0)
})

const { goToPrevious, goToNext } = branchContext
</script>

<style scoped>
.message-branch-selector {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-top: 10px;
  padding: 3px 8px;
  background: var(--el-fill-color-light);
  border-radius: 16px;
  border: 1px solid var(--el-border-color-lighter);
}

.branch-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--el-text-color-secondary);
  cursor: pointer;
  transition: all 0.15s ease;
  padding: 0;
}

.branch-btn:hover:not(:disabled) {
  background: var(--el-fill-color);
  color: var(--el-text-color-primary);
}

.branch-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

.branch-page {
  display: flex;
  align-items: center;
  gap: 2px;
  font-size: 12px;
  color: var(--el-text-color-regular);
  padding: 0 4px;
  user-select: none;
  line-height: 1;
}

.branch-current {
  font-weight: 600;
  color: var(--el-color-primary);
}

.branch-sep {
  color: var(--el-text-color-placeholder);
  margin: 0 1px;
}

.branch-total {
  color: var(--el-text-color-placeholder);
}
</style>
