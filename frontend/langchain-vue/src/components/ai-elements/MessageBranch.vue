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
  totalBranches: {
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

const goToPrevious = () => {
  if (props.totalBranches <= 1) return
  const newBranch = currentBranch.value > 0
    ? currentBranch.value - 1
    : props.totalBranches - 1
  handleBranchChange(newBranch)
}

const goToNext = () => {
  if (props.totalBranches <= 1) return
  const newBranch = currentBranch.value < props.totalBranches - 1
    ? currentBranch.value + 1
    : 0
  handleBranchChange(newBranch)
}

const handleBranchChange = (newBranch) => {
  currentBranch.value = newBranch
  emit('branchChange', newBranch)
}

provide('messageBranchContext', {
  currentBranch,
  totalBranches: computed(() => props.totalBranches),
  goToPrevious,
  goToNext,
})

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
