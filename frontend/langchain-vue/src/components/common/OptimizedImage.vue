<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'

const props = defineProps({
  src: {
    type: String,
    default: ''
  },
  alt: {
    type: String,
    default: ''
  },
  fit: {
    type: String,
    default: 'cover'
  },
  lazy: {
    type: Boolean,
    default: false
  },
  showPreview: {
    type: Boolean,
    default: true
  },
  previewSrc: {
    type: String,
    default: ''
  }
})

const emit = defineEmits(['load', 'error', 'click'])

const imgSrc = ref('')
const isLoading = ref(true)
const hasError = ref(false)
const imgRef = ref(null)

const previewSrc = computed(() => {
  return props.previewSrc || props.src
})

const handleLoad = () => {
  isLoading.value = false
  emit('load')
}

const handleError = () => {
  isLoading.value = false
  hasError.value = true
  emit('error')
}

const handleClick = () => {
  emit('click', previewSrc.value)
}

onMounted(() => {
  if (props.lazy && 'IntersectionObserver' in window) {
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          imgSrc.value = props.src
          observer.unobserve(entry.target)
        }
      })
    })
    
    if (imgRef.value) {
      observer.observe(imgRef.value)
    }
    
    onUnmounted(() => {
      observer.disconnect()
    })
  } else {
    imgSrc.value = props.src
  }
})

watch(() => props.src, (newSrc) => {
  isLoading.value = true
  hasError.value = false
  imgSrc.value = newSrc
})
</script>

<template>
  <div
    class="optimized-image"
    :class="{
      'is-loading': isLoading,
      'has-error': hasError,
      'is-lazy': lazy
    }"
    @click="handleClick"
  >
    <div v-if="isLoading" class="image-placeholder">
      <el-icon :size="32" class="loading-icon">
        <Loading />
      </el-icon>
    </div>
    
    <img
      v-if="!hasError"
      ref="imgRef"
      :src="imgSrc"
      :alt="alt"
      :class="['image-content', `fit-${fit}`]"
      loading="lazy"
      @load="handleLoad"
      @error="handleError"
    >
    
    <div v-if="hasError" class="image-error">
      <el-icon :size="32" class="error-icon">
        <Picture />
      </el-icon>
      <span class="error-text">加载失败</span>
    </div>
  </div>
</template>

<style scoped>
.optimized-image {
  position: relative;
  overflow: hidden;
  background-color: #f5f7fa;
  border-radius: 8px;
}

.image-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  background-color: #f5f7fa;
}

.loading-icon {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  0% {
    transform: rotate(0deg);
  }
  100% {
    transform: rotate(360deg);
  }
}

.image-content {
  width: 100%;
  height: 100%;
  opacity: 0;
  transition: opacity 0.3s ease;
}

.image-content.loaded {
  opacity: 1;
}

.fit-cover {
  object-fit: cover;
}

.fit-contain {
  object-fit: contain;
}

.fit-fill {
  object-fit: fill;
}

.fit-none {
  object-fit: none;
}

.image-error {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  color: #909399;
}

.error-icon {
  margin-bottom: 8px;
}

.error-text {
  font-size: 12px;
}

.is-lazy {
  min-height: 100px;
}
</style>
