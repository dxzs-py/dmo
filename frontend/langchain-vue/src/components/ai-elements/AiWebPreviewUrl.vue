<template>
  <el-input
    v-model="inputValue"
    :class="className"
    size="small"
    placeholder="Enter URL..."
    @keydown="handleKeyDown"
  />
</template>

<script setup>
import { ref, inject, watch } from 'vue'

const props = defineProps({
  className: {
    type: String,
    default: ''
  },
  modelValue: {
    type: String,
    default: ''
  }
})

const emit = defineEmits(['update:modelValue', 'change', 'keydown'])

const webPreview = inject('webPreview')
const inputValue = ref(props.modelValue || webPreview?.url.value || '')

watch(() => webPreview?.url.value, (newUrl) => {
  inputValue.value = newUrl
})

watch(() => props.modelValue, (newValue) => {
  inputValue.value = newValue
})

const handleKeyDown = (event) => {
  emit('keydown', event)
  if (event.key === 'Enter') {
    if (webPreview) {
      webPreview.setUrl(inputValue.value)
    }
    emit('update:modelValue', inputValue.value)
  }
}
</script>

<style scoped>
</style>
