<template>
  <el-button
    size="small"
    :disabled="totalBranchesValue <= 1"
    :class="className"
    v-bind="$attrs"
    @click="goToNext"
  >
    <el-icon><ArrowRight /></el-icon>
  </el-button>
</template>

<script setup>
import { inject, computed } from 'vue'
import { ArrowRight } from '@element-plus/icons-vue'

defineProps({
  className: {
    type: String,
    default: ''
  }
})

const branchContext = inject('messageBranchContext')

if (!branchContext) {
  throw new Error('MessageBranchNext must be used within MessageBranch')
}

const { goToNext } = branchContext

const totalBranchesValue = computed(() => {
  const val = branchContext.totalBranches
  return typeof val === 'object' && val !== null && 'value' in val ? val.value : (val ?? 0)
})
</script>
