<template>
  <div class="message-branch" :class="className" v-bind="$attrs">
    <slot></slot>
  </div>
</template>

<script setup>
import { ref, provide, watch, computed } from 'vue'

const props = defineProps({
  defaultBranch: {
    type: Number,
    default: 0
  },
  className: {
    type: String,
    default: ''
  }
})

const emit = defineEmits(['branchChange'])

const currentBranch = ref(props.defaultBranch)
const branches = ref([])

const totalBranches = computed(() => branches.value.length)

const goToPrevious = () => {
  if (totalBranches.value <= 1) return
  const newBranch = currentBranch.value > 0 
    ? currentBranch.value - 1 
    : totalBranches.value - 1
  handleBranchChange(newBranch)
}

const goToNext = () => {
  if (totalBranches.value <= 1) return
  const newBranch = currentBranch.value < totalBranches.value - 1 
    ? currentBranch.value + 1 
    : 0
  handleBranchChange(newBranch)
}

const handleBranchChange = (newBranch) => {
  currentBranch.value = newBranch
  emit('branchChange', newBranch)
}

const setBranches = (newBranches) => {
  branches.value = newBranches
}

// 提供上下文
provide('messageBranchContext', {
  currentBranch,
  branches,
  totalBranches,
  goToPrevious,
  goToNext,
  setBranches
})

// 监听defaultBranch变化
watch(() => props.defaultBranch, (newValue) => {
  currentBranch.value = newValue
})
</script>

<style scoped>
.message-branch {
  width: 100%;
  display: grid;
  gap: 8px;
}

.message-branch > div {
  padding-bottom: 0;
}
</style>