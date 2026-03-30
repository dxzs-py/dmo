<template>
  <div class="ai-web-preview" :class="className">
    <slot></slot>
  </div>
</template>

<script setup>
import { ref, provide } from 'vue'

const props = defineProps({
  className: {
    type: String,
    default: ''
  },
  defaultUrl: {
    type: String,
    default: ''
  }
})

const emit = defineEmits(['url-change'])

const url = ref(props.defaultUrl)
const consoleOpen = ref(false)

const setUrl = (newUrl) => {
  url.value = newUrl
  emit('url-change', newUrl)
}

const setConsoleOpen = (open) => {
  consoleOpen.value = open
}

provide('webPreview', {
  url,
  setUrl,
  consoleOpen,
  setConsoleOpen
})
</script>

<style scoped>
.ai-web-preview {
  display: flex;
  width: 100%;
  height: 100%;
  flex-direction: column;
  border-radius: 8px;
  border: 1px solid var(--el-border-color);
  background-color: var(--el-bg-color);
}
</style>
