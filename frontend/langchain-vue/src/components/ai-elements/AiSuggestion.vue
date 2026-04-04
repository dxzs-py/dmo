<template>
  <el-button 
    :class="['ai-suggestion', className]" 
    :variant="variant" 
    :size="size"
    @click="handleClick"
    v-bind="$attrs"
  >
    {{ children || suggestion }}
  </el-button>
</template>

<script setup>
import { useSlots } from 'vue'

const props = defineProps({
  suggestion: {
    type: String,
    required: true
  },
  className: {
    type: String,
    default: ''
  },
  variant: {
    type: String,
    default: 'default'
  },
  size: {
    type: String,
    default: 'small'
  }
})

const emit = defineEmits(['click'])

const children = useSlots().default ? useSlots().default() : null

const handleClick = () => {
  emit('click', props.suggestion)
}
</script>

<style scoped>
.ai-suggestion {
  cursor: pointer;
  border-radius: 20px;
  padding: 6px 16px;
  white-space: nowrap;
}
</style>