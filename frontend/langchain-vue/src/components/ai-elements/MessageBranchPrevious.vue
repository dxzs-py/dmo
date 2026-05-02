<template>
  <el-button
    size="small"
    :disabled="totalBranchesValue <= 1"
    :class="className"
    v-bind="$attrs"
    @click="goToPrevious"
  >
    <el-icon><ArrowLeft /></el-icon>
  </el-button>
</template>

<script setup>
import { inject, computed } from 'vue'
import { ArrowLeft } from '@element-plus/icons-vue'

defineProps({
  className: {
    type: String,
    default: ''
  }
})

const branchContext = inject('messageBranchContext')

if (!branchContext) {
  throw new Error('MessageBranchPrevious must be used within MessageBranch')
}

const { goToPrevious } = branchContext

const totalBranchesValue = computed(() => {
  const val = branchContext.totalBranches
  return typeof val === 'object' && val !== null && 'value' in val ? val.value : (val ?? 0)
})
</script>
