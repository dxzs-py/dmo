<template>
  <div class="ai-web-preview-body">
    <iframe
      :class="className"
      :src="src || (webPreview?.url.value || undefined)"
      sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-presentation"
      title="Preview"
    />
    <slot v-if="loading" name="loading"></slot>
  </div>
</template>

<script setup>
import { inject } from 'vue'

const webPreview = inject('webPreview')

defineProps({
  className: {
    type: String,
    default: ''
  },
  loading: {
    type: [Boolean, Object],
    default: false
  },
  src: {
    type: String,
    default: ''
  }
})
</script>

<style scoped>
.ai-web-preview-body {
  flex: 1;
}

.ai-web-preview-body iframe {
  width: 100%;
  height: 100%;
  border: none;
}
</style>
