<template>
  <div v-for="(branch, index) in branchChildren" :key="index" 
       :class="{ 'block': index === currentBranch, 'hidden': index !== currentBranch }"
       v-bind="$attrs">
    <component :is="branch" />
  </div>
</template>

<script setup>
import { inject, watch, computed, useSlots } from 'vue'

defineProps({
  className: {
    type: String,
    default: ''
  }
})

const slots = useSlots()
const branchContext = inject('messageBranchContext')

if (!branchContext) {
  throw new Error('MessageBranchContent must be used within MessageBranch')
}

const { currentBranch, setBranches } = branchContext

const branchChildren = computed(() => {
  return slots.default ? slots.default() : []
})

// 监听分支变化并更新
watch(branchChildren, (newChildren) => {
  setBranches(newChildren)
}, { deep: true })
</script>

<style scoped>
.block {
  display: block;
}

.hidden {
  display: none;
}
</style>